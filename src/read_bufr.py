#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lê produtos Sentinel-3A/B SLSTR AMV diretamente de arquivos ZIP.
Gera um CSV e um GeoJSON para cada arquivo BUFR encontrado.

Estrutura padrão:
  vento_s3/data/sentinel3/*.zip
  vento_s3/output/csv/*.csv
  vento_s3/output/geojson/*.geojson

Dependências:
  pip install eccodes numpy pandas
  (a biblioteca de sistema ecCodes também deve estar instalada)
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import zipfile
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from eccodes import (
    codes_get,
    codes_get_array,
    codes_new_from_message,
    codes_release,
    codes_set,
)

LOG = logging.getLogger("read_bufr")


def as_1d_array(values) -> np.ndarray:
    """Normaliza o retorno do ecCodes para um vetor NumPy unidimensional."""
    return np.asarray(values).reshape(-1)


def get_array(gid, candidates: Iterable[str], required: bool = True):
    """Obtém o primeiro campo disponível entre nomes alternativos."""
    errors = []
    for key in candidates:
        try:
            return as_1d_array(codes_get_array(gid, key))
        except Exception as exc:
            errors.append(f"{key}: {exc}")

    if required:
        raise KeyError(
            "Não foi possível obter nenhum dos campos: "
            + ", ".join(candidates)
            + ". Tentativas: "
            + " | ".join(errors)
        )
    return None


def get_scalar(gid, candidates: Iterable[str], default=None):
    for key in candidates:
        try:
            return codes_get(gid, key)
        except Exception:
            pass
    return default


def sensing_datetime_from_name(filename: str) -> str | None:
    """Extrai AAAAMMDDThhmmss do nome padrão do produto Sentinel-3."""
    match = re.search(r"_(\d{8}T\d{6})_", filename)
    if not match:
        return None
    from datetime import datetime
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def decode_bufr(bufr_bytes: bytes, zip_name: str, bufr_name: str) -> pd.DataFrame:
    gid = codes_new_from_message(bufr_bytes)
    if gid is None:
        raise RuntimeError(f"ecCodes não conseguiu abrir {bufr_name}")

    try:
        codes_set(gid, "unpack", 1)

        n_subsets = int(get_scalar(gid, ["numberOfSubsets"], 0) or 0)
        satellite = get_scalar(
            gid,
            ["satelliteIdentifier", "#1#satelliteIdentifier"],
            default=None,
        )
        if satellite is not None:
            try:
                satellite = int(satellite)
            except (TypeError, ValueError):
                pass

        # Os nomes abaixo correspondem aos campos identificados no BUFR
        # AMV decodificado. Mantemos alternativas para pequenas variações.
        fields = {
            "latitude": get_array(gid, ["#1#latitude", "latitude"]),
            "longitude": get_array(gid, ["#1#longitude", "longitude"]),
            "pressure_pa": get_array(gid, ["#1#pressure", "pressure"]),
            "wind_direction_deg": get_array(
                gid, ["windDirection", "#1#windDirection"]
            ),
            "wind_speed_ms": get_array(
                gid, ["windSpeed", "#1#windSpeed"]
            ),
            "tracking_correlation": get_array(
                gid,
                ["#1#trackingCorrelationOfVector",
                 "trackingCorrelationOfVector"],
                required=False,
            ),
        }

        lengths = {name: len(values) for name, values in fields.items()
                   if values is not None}
        required_lengths = {
            name: length for name, length in lengths.items()
            if name != "tracking_correlation"
        }
        if len(set(required_lengths.values())) != 1:
            raise ValueError(
                f"Campos vetoriais com comprimentos diferentes: {required_lengths}"
            )

        n = next(iter(required_lengths.values()))
        if n == 0:
            raise ValueError("BUFR não contém vetores AMV.")

        if fields["tracking_correlation"] is None:
            fields["tracking_correlation"] = np.full(n, np.nan)
        elif len(fields["tracking_correlation"]) != n:
            LOG.warning(
                "Correlação tem %d valores; esperado %d. Será preenchida com NaN.",
                len(fields["tracking_correlation"]), n
            )
            corr = np.full(n, np.nan)
            m = min(n, len(fields["tracking_correlation"]))
            corr[:m] = fields["tracking_correlation"][:m]
            fields["tracking_correlation"] = corr

        df = pd.DataFrame({key: value for key, value in fields.items()})
        df.insert(0, "vector_id", np.arange(1, len(df) + 1))
        df["pressure_hpa"] = pd.to_numeric(
            df["pressure_pa"], errors="coerce"
        ) / 100.0
        df["datetime"] = sensing_datetime_from_name(zip_name)
        df["satellite"] = satellite
        df["source_zip"] = zip_name
        df["source_bufr"] = bufr_name

        # Converte campos numéricos e remove apenas registros sem posição
        # ou sem os dois componentes necessários à representação do vento.
        numeric = [
            "latitude", "longitude", "pressure_pa", "pressure_hpa",
            "wind_direction_deg", "wind_speed_ms", "tracking_correlation"
        ]
        for col in numeric:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        valid = (
            df["latitude"].between(-90, 90)
            & df["longitude"].between(-180, 180)
            & df["wind_direction_deg"].between(0, 360)
            & (df["wind_speed_ms"] >= 0)
        )
        removed = int((~valid).sum())
        if removed:
            LOG.warning("%s: %d registros inválidos removidos.", bufr_name, removed)
        df = df.loc[valid].reset_index(drop=True)
        df["vector_id"] = np.arange(1, len(df) + 1)

        LOG.info(
            "%s: %d subsets no cabeçalho; %d vetores válidos decodificados.",
            bufr_name, n_subsets, len(df)
        )
        return df
    finally:
        codes_release(gid)


def dataframe_to_geojson(df: pd.DataFrame) -> dict:
    features = []
    for row in df.to_dict(orient="records"):
        lon = float(row["longitude"])
        lat = float(row["latitude"])
        properties = {}
        for key, value in row.items():
            if key in ("longitude", "latitude"):
                continue
            if pd.isna(value):
                value = None
            elif isinstance(value, np.generic):
                value = value.item()
            properties[key] = value

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat],
            },
            "properties": properties,
        })

    return {
        "type": "FeatureCollection",
        "name": "Sentinel-3 AMV",
        "features": features,
    }


def process_zip(zip_path: Path, csv_dir: Path, geojson_dir: Path) -> int:
    with zipfile.ZipFile(zip_path, "r") as zf:
        bufr_members = [
            name for name in zf.namelist()
            if name.lower().endswith(".bufr") and not name.endswith("/")
        ]

        if not bufr_members:
            LOG.warning("Nenhum .bufr encontrado em %s", zip_path.name)
            return 0

        total = 0
        for member in bufr_members:
            LOG.info("Lendo %s :: %s", zip_path.name, member)
            content = zf.read(member)  # leitura direta; não extrai o ZIP
            df = decode_bufr(content, zip_path.name, Path(member).name)

            stem = zip_path.stem
            if len(bufr_members) > 1:
                stem += "_" + Path(member).stem

            csv_path = csv_dir / f"{stem}.csv"
            geojson_path = geojson_dir / f"{stem}.geojson"

            df.to_csv(csv_path, index=False, encoding="utf-8")
            with geojson_path.open("w", encoding="utf-8") as f:
                json.dump(
                    dataframe_to_geojson(df), f,
                    ensure_ascii=False, indent=2, allow_nan=False
                )

            LOG.info("CSV: %s", csv_path)
            LOG.info("GeoJSON: %s", geojson_path)
            total += len(df)

    return total


def main():
    parser = argparse.ArgumentParser(
        description="Decodifica BUFR AMV Sentinel-3 dentro de ZIP e gera CSV/GeoJSON."
    )
    parser.add_argument(
        "--input", type=Path, default=None,
        help="Diretório dos ZIPs (padrão: <projeto>/data/sentinel3)."
    )
    parser.add_argument(
        "--csv-dir", type=Path, default=None,
        help="Diretório CSV (padrão: <projeto>/output/csv)."
    )
    parser.add_argument(
        "--geojson-dir", type=Path, default=None,
        help="Diretório GeoJSON (padrão: <projeto>/output/geojson)."
    )
    parser.add_argument(
        "--recursive", action="store_true",
        help="Procura ZIPs também em subdiretórios."
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    input_dir = (args.input or project_root / "data" / "sentinel3").resolve()
    csv_dir = (args.csv_dir or project_root / "output" / "csv").resolve()
    geojson_dir = (
        args.geojson_dir or project_root / "output" / "geojson"
    ).resolve()

    if not input_dir.is_dir():
        raise SystemExit(f"Diretório de entrada não encontrado: {input_dir}")

    csv_dir.mkdir(parents=True, exist_ok=True)
    geojson_dir.mkdir(parents=True, exist_ok=True)

    pattern = "**/*.zip" if args.recursive else "*.zip"
    zip_files = sorted(input_dir.glob(pattern))
    if not zip_files:
        LOG.warning("Nenhum ZIP encontrado em %s", input_dir)
        return

    LOG.info("ZIPs encontrados: %d", len(zip_files))
    processed = 0
    vectors = 0
    for zip_path in zip_files:
        try:
            count = process_zip(zip_path, csv_dir, geojson_dir)
            if count:
                processed += 1
                vectors += count
        except Exception:
            LOG.exception("Falha ao processar %s", zip_path.name)

    LOG.info(
        "Concluído: %d ZIP(s) com dados; %d vetores válidos.", processed, vectors
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    main()
