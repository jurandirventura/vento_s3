# vento_s3 — processamento e visualização de vetores AMV

## Conteúdo
- `src/read_bufr.py`: lê `.bufr` diretamente de cada ZIP Sentinel-3 e gera CSV + GeoJSON.
- `src/plot_amv_map.py`: lê GeoJSON e gera mapa HTML interativo.

## Dependências
No ambiente Conda `vento_s3`:

```bash
pip install eccodes pandas folium branca
```

A biblioteca ecCodes do sistema também deve estar instalada. Em Ubuntu/WSL:

```bash
sudo apt update
sudo apt install libeccodes-tools libeccodes-data
```

## 1. Processar os BUFR
A partir da raiz `~/vento_s3`:

```bash
python src/read_bufr.py
```

Saídas:
- `output/csv/<nome_do_zip>.csv`
- `output/geojson/<nome_do_zip>.geojson`

Para reprocessar e sobrescrever:

```bash
python src/read_bufr.py --overwrite
```

Para buscar ZIPs em subdiretórios:

```bash
python src/read_bufr.py --recursive
```

## 2. Gerar mapa
Depois de gerar os GeoJSON:

```bash
python src/plot_amv_map.py
```

Saída:
- `output/maps/sentinel3_amv_map.html`

## 3. Abrir o mapa no navegador (recomendado)

Evite abrir diretamente pelo endereço `file:///`. Sirva o HTML por HTTP local:

```bash
python -m http.server 8000 --directory output/maps
```

Acesse no navegador:

```text
http://127.0.0.1:8000/sentinel3_amv_map.html
```

Mantenha o terminal com o servidor aberto enquanto visualizar o mapa. Para encerrar,
use `Ctrl+C`.

O mapa não utiliza o servidor público de tiles do OpenStreetMap. O mapa-base inicial
é Esri World Imagery (satélite), com CARTO Positron como alternativa no controle de
camadas. Esses mapas-base dependem de conexão com a Internet e estão sujeitos às
condições de uso de seus respectivos provedores.

Para gerar o mapa de um único produto:

```bash
python src/plot_amv_map.py --input output/geojson/NOME_DO_PRODUTO.geojson
```

Para iniciar com o mapa claro CARTO, em vez da imagem de satélite:

```bash
python src/plot_amv_map.py --basemap carto
```

## Parâmetros úteis
Correlação mínima inicial (o controle deslizante do próprio mapa pode ser ajustado no navegador):

```bash
python src/plot_amv_map.py --min-correlation 0.50
```

Comprimento gráfico das setas:

```bash
python src/plot_amv_map.py --arrow-length-km 60
```

O comprimento da seta é apenas uma escala visual para facilitar a leitura espacial;
não representa a distância percorrida durante o intervalo de observação.

## Observação sobre a direção
A rotina considera `windDirection` como direção meteorológica, isto é, de onde o vento vem,
e desenha a seta no sentido para onde o vento se desloca (direção + 180 graus).
Essa convenção deve ser confirmada com a documentação do produto AMV antes de usar
as setas em análises científicas. Se o campo já indicar o sentido do deslocamento,
execute com `--direction-is-to`.

## Validação
Compare:
- número de registros do CSV e número de `features` do GeoJSON;
- quantidade de vetores com `numberOfSubsets` exibida pelo BUFR;
- posições, pressão e velocidade com o resultado já decodificado pelo `bufr_dump`.

A rotina não aplica filtro automático de qualidade: o limiar de correlação é aplicado
interativamente no mapa, para permitir avaliar a distribuição antes de descartar dados.



## Mapa-base Stadia Alidade Smooth

O script aceita três opções de mapa-base inicial:

- `--basemap imagery`: Esri World Imagery (satélite), padrão;
- `--basemap carto`: CARTO Positron;
- `--basemap stadia`: Stadia Alidade Smooth.

Para desenvolvimento local, acessando o mapa por `http://127.0.0.1:8000/` ou `http://localhost:8000/`, o Stadia Maps permite carregar os tiles sem chave de API (sujeito a limites de uso). Portanto, basta executar:

```bash
python src/plot_amv_map.py --basemap stadia
python -m http.server 8000 --directory output/maps
```

Depois acesse `http://127.0.0.1:8000/sentinel3_amv_map.html`. Não abra o HTML diretamente com `file://`, pois a autenticação gratuita para desenvolvimento local depende do acesso por localhost.

Se futuramente o mapa for publicado em um domínio ou utilizado fora do ambiente local, consulte a documentação do Stadia Maps para configurar a autenticação adequada. Opcionalmente, o script aceita uma chave pela variável `STADIA_API_KEY`:

```bash
export STADIA_API_KEY='SUA_CHAVE_DE_API'
python src/plot_amv_map.py --basemap stadia
```

Documentação oficial: [Alidade Smooth](https://docs.stadiamaps.com/map-styles/alidade-smooth/) e [autenticação](https://docs.stadiamaps.com/authentication/).
