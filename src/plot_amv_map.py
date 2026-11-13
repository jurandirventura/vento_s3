#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gera mapa HTML interativo dos vetores AMV a partir dos GeoJSON produzidos
por read_bufr.py.

Uso:
    python src/plot_amv_map.py
    python src/plot_amv_map.py --input output/geojson/arquivo.geojson
    python src/plot_amv_map.py --min-correlation 0.5 --arrow-length-km 50

O mapa permite:
- colorir vetores pela velocidade do vento;
- consultar atributos em popups;
- filtrar interativamente pela correlação de rastreamento;
- controlar camadas e mapa-base.

A direção BUFR windDirection é interpretada por padrão como direção
meteorológica "de onde vem" o vento. Assim, a seta é desenhada para
windDirection + 180 graus (sentido para onde o vento se desloca).
Se a convenção do produto for diferente, use --direction-is-to.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from urllib.parse import quote

import folium
import pandas as pd
from branca.colormap import LinearColormap
from folium.plugins import Fullscreen, MousePosition, PolyLineTextPath


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "output" / "geojson"
DEFAULT_OUTPUT = ROOT / "output" / "maps"


def parse_args():
    p = argparse.ArgumentParser(description="Visualiza vetores Sentinel-3 AMV em mapa.")
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                   help="Arquivo GeoJSON ou diretório contendo GeoJSONs.")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--output", type=Path, default=None,
                   help="Nome/caminho HTML de saída opcional.")
    p.add_argument("--min-correlation", type=float, default=0.0,
                   help="Correlação mínima inicial (0 a 1).")
    p.add_argument("--arrow-length-km", type=float, default=40.0,
                   help="Comprimento gráfico das setas em km.")
    p.add_argument("--direction-is-to", action="store_true",
                   help="Interpreta windDirection como direção para onde o vento vai.")
    p.add_argument("--max-vectors", type=int, default=0,
                   help="Limite opcional para teste; 0 significa todos.")
    p.add_argument("--basemap", choices=("imagery", "carto", "stadia"), default="imagery",
                   help="Mapa-base inicial: imagery (Esri), carto ou stadia (Alidade Smooth).")
    return p.parse_args()


def destination(lat, lon, bearing_deg, distance_km):
    """Destino aproximado sobre esfera; retorna latitude, longitude."""
    radius = 6371.0088
    bearing = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    angular = distance_km / radius

    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular)
        + math.cos(lat1) * math.sin(angular) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), ((math.degrees(lon2) + 540) % 360) - 180


def read_geojson(path: Path) -> pd.DataFrame:
    with path.open(encoding="utf-8") as f:
        obj = json.load(f)
    rows = []
    for feature in obj.get("features", []):
        geom = feature.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        lon, lat = geom["coordinates"][:2]
        row = dict(feature.get("properties") or {})
        row["longitude"] = lon
        row["latitude"] = lat
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    args = parse_args()
    if not 0 <= args.min_correlation <= 1:
        raise SystemExit("--min-correlation deve estar entre 0 e 1.")
    if args.arrow_length_km <= 0:
        raise SystemExit("--arrow-length-km deve ser positivo.")

    # O Stadia permite uso local sem chave quando o HTML é servido por
    # localhost/127.0.0.1. A chave é opcional e pode ser definida para outros usos.
    stadia_api_key = os.environ.get("STADIA_API_KEY", "").strip()

    if args.input.is_file():
        files = [args.input]
    elif args.input.is_dir():
        files = sorted(args.input.glob("*.geojson"))
    else:
        files = []

    if not files:
        raise SystemExit(f"Nenhum GeoJSON encontrado em: {args.input}")

    frames = []
    for file in files:
        df = read_geojson(file)
        if not df.empty:
            frames.append(df)
    if not frames:
        raise SystemExit("Os GeoJSON não contêm feições Point.")

    df = pd.concat(frames, ignore_index=True)
    required = ["latitude", "longitude", "wind_direction_deg", "wind_speed_ms"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SystemExit(f"Campos ausentes nos GeoJSON: {missing}")

    for col in required + ["tracking_correlation", "pressure_hpa"]:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=required).copy()
    if "tracking_correlation" not in df:
        df["tracking_correlation"] = 0.0
    df["tracking_correlation"] = df["tracking_correlation"].fillna(0.0).clip(0, 1)

    if args.max_vectors > 0:
        df = df.head(args.max_vectors)
    if df.empty:
        raise SystemExit("Nenhum vetor válido para mapear.")

    center = [float(df["latitude"].mean()), float(df["longitude"].mean())]
    # Não usar o servidor público de tiles do OpenStreetMap como mapa-base.
    # O serviço OSM pode bloquear requisições que não respeitem sua política.
    # Oferecemos Esri, CARTO e Stadia como mapas-base alternativos.
    m = folium.Map(location=center, zoom_start=3, tiles=None,
                   control_scale=True, prefer_canvas=False)

    imagery = folium.TileLayer(
        tiles=("https://server.arcgisonline.com/ArcGIS/rest/services/"
               "World_Imagery/MapServer/tile/{z}/{y}/{x}"),
        attr=("Tiles © Esri — Sources: Esri, Maxar, Earthstar Geographics, "
              "and the GIS User Community"),
        name="Imagem de satélite (Esri)",
        overlay=False,
        control=True,
        show=(args.basemap == "imagery"),
    )
    imagery.add_to(m)

    # CARTO pode exigir chave de API dependendo do serviço/conta utilizada.
    # Mantido como opção legada; Stadia usa explicitamente STADIA_API_KEY.
    carto = folium.TileLayer(
        tiles="CartoDB positron",
        name="Mapa claro (CARTO)",
        overlay=False,
        control=True,
        show=(args.basemap == "carto"),
    )
    carto.add_to(m)

    stadia_url = (
        "https://tiles.stadiamaps.com/tiles/alidade_smooth/"
        "{z}/{x}/{y}{r}.png"
    )
    if stadia_api_key:
        stadia_url += "?api_key=" + quote(stadia_api_key, safe="")
    stadia = folium.TileLayer(
        tiles=stadia_url,
        attr=('© <a href="https://stadiamaps.com/">Stadia Maps</a> '
              '© <a href="https://openmaptiles.org/">OpenMapTiles</a> '
              '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'),
        name="Stadia Alidade Smooth",
        overlay=False,
        control=True,
        show=(args.basemap == "stadia"),
        max_zoom=20,
    )
    stadia.add_to(m)

    vmin = max(0.0, float(df["wind_speed_ms"].min()))
    vmax = max(vmin + 0.1, float(df["wind_speed_ms"].max()))
    cmap = LinearColormap(
        ["#2c7bb6", "#abd9e9", "#ffffbf", "#fdae61", "#d7191c"],
        vmin=vmin, vmax=vmax,
        caption="Velocidade do vento (m/s)",
    )
    cmap.add_to(m)

    vector_layers = []
    for _, row in df.iterrows():
        lat = float(row["latitude"])
        lon = float(row["longitude"])
        direction = float(row["wind_direction_deg"])
        speed = float(row["wind_speed_ms"])
        corr = float(row["tracking_correlation"])

        # BUFR windDirection segue a convenção meteorológica (FROM) por padrão.
        bearing_to = direction if args.direction_is_to else (direction + 180.0) % 360.0
        end_lat, end_lon = destination(lat, lon, bearing_to, args.arrow_length_km)
        color = cmap(speed)

        popup_html = (
            f"<b>Vetor AMV #{row.get('vector_id', '')}</b><br>"
            f"Data/hora: {row.get('datetime', 'N/D')}<br>"
            f"Satélite: {row.get('satellite', 'N/D')}<br>"
            f"Latitude: {lat:.5f}<br>Longitude: {lon:.5f}<br>"
            f"Pressão: {row.get('pressure_hpa', float('nan')):.1f} hPa<br>"
            f"Direção: {direction:.0f}°<br>"
            f"Velocidade: {speed:.1f} m/s<br>"
            f"Correlação: {corr:.3f}"
        )
        popup = folium.Popup(popup_html, max_width=300)

        line = folium.PolyLine(
            locations=[[lat, lon], [end_lat, end_lon]],
            color=color, weight=2.5, opacity=0.85,
            tooltip=f"{speed:.1f} m/s | direção {direction:.0f}°",
        )
        line.add_to(m)
        # Seta repetida sobre a linha para indicar o sentido do deslocamento.
        arrow = PolyLineTextPath(
            line, "➤", repeat=False, offset=7,
            attributes={"fill": color, "font-weight": "bold", "font-size": "14"},
        )
        arrow.add_to(m)

        marker = folium.CircleMarker(
            location=[lat, lon],
            radius=3.5,
            color=color,
            weight=1,
            fill=True,
            fill_color=color,
            fill_opacity=0.9,
            popup=popup,
        )
        marker.add_to(m)

        vector_layers.append({
            "line": line.get_name(),
            "marker": marker.get_name(),
            "correlation": corr,
        })

    # Controle interativo: o usuário ajusta a correlação mínima e o mapa
    # atualiza a opacidade das linhas, setas e pontos sem recarregar o HTML.
    map_name = m.get_name()
    layer_data = json.dumps(vector_layers)
    control_html = f"""
    <div id="amv-filter" style="
      position: fixed; top: 12px; left: 55px; z-index: 9999;
      background: white; padding: 10px 12px; border: 1px solid #777;
      border-radius: 5px; box-shadow: 0 1px 5px #777; width: 265px;
      font: 13px Arial, sans-serif;">
      <b>Filtro de qualidade AMV</b><br>
      Correlação mínima: <span id="corr-value">{args.min_correlation:.2f}</span>
      <input id="corr-slider" type="range" min="0" max="1" step="0.01"
             value="{args.min_correlation}" style="width:100%;">
      <small>Vetores visíveis: <span id="visible-count">0</span> / {len(df)}</small>
    </div>
    """
    # O HTML do controle fica no corpo; o JavaScript é inserido na seção
    # de scripts do Folium para executar depois da criação do mapa e das linhas.
    m.get_root().html.add_child(folium.Element(control_html))

    filter_js = f"""
    (function() {{
      function initAmvFilter() {{
        var layers = {layer_data};
        var slider = document.getElementById('corr-slider');
        var label = document.getElementById('corr-value');
        var count = document.getElementById('visible-count');

        if (!slider || !label || !count) {{
          console.error('Controle AMV: elementos HTML não encontrados.');
          return;
        }}

        function update() {{
          var threshold = parseFloat(slider.value);
          var visible = 0;

          layers.forEach(function(item) {{
            var show = item.correlation >= threshold;
            if (show) visible++;

            var line = window[item.line];
            var marker = window[item.marker];

            if (line) {{
              line.setStyle({{opacity: show ? 0.85 : 0.03}});
              // PolyLineTextPath aplica setText() à própria PolyLine.
              if (typeof line.setText === 'function') {{
                line.setText(show ? '➤' : '', {{
                  repeat: false,
                  offset: 7,
                  attributes: {{
                    fill: '#333333',
                    'font-weight': 'bold',
                    'font-size': '14'
                  }}
                }});
              }}
            }}

            if (marker) {{
              marker.setStyle({{
                opacity: show ? 1 : 0.03,
                fillOpacity: show ? 0.9 : 0.02
              }});
            }}
          }});

          label.textContent = threshold.toFixed(2);
          count.textContent = visible;
        }}

        slider.addEventListener('input', update);
        update();
      }}

      // Aguarda o DOM e a inicialização do mapa Folium.
      if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', initAmvFilter);
      }} else {{
        initAmvFilter();
      }}
    }})();
    """
    m.get_root().script.add_child(folium.Element(filter_js))

    Fullscreen(position="topright").add_to(m)
    MousePosition(position="bottomright", separator=" | ",
                  prefix="Lat/Lon:").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    output_dir = args.output.parent if args.output else args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output or (output_dir / "sentinel3_amv_map.html")
    m.save(str(output))

    print(f"[OK] Mapa salvo: {output}")
    print(f"[INFO] Vetores: {len(df)}")
    print(f"[INFO] Correlação mínima inicial: {args.min_correlation:.2f}")
    print(f"[INFO] Comprimento gráfico das setas: {args.arrow_length_km:.1f} km")
    print(f"[INFO] Mapa-base inicial: {args.basemap}")
    print("[INFO] Para abrir via HTTP local, execute na raiz do projeto:")
    print("       python -m http.server 8001 --directory output/maps")
    print("       e acesse http://127.0.0.1:8001/sentinel3_amv_map.html")


if __name__ == "__main__":
    main()
