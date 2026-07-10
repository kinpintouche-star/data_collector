# Guide Archive R2 - Donnees Marche

Derniere mise a jour: 2026-07-09

## Objectif Maintenant

Le chemin cible des donnees gratuites devient:

```text
Providers gratuits -> GitHub Actions -> R2 archive chiffree -> PC local -> Postgres local -> backtests
```

Le pipeline live gratuit ne passe plus par une base remote SQL. GitHub Actions archive chaque jour les candles recuperees directement dans R2, puis le PC restaure localement uniquement les partitions utiles quand on veut lancer un backtest.

Contraintes non negociables:

- gratuit d'abord;
- bucket R2 garde sous 10 GB par defaut;
- Databento jamais automatique;
- pas de source payante dans les workflows schedules;
- la base locale Postgres reste la base canonique pour backtester;
- R2 stocke des fichiers compresses puis chiffres;
- le backtest ne lit pas directement R2, il lit localement.

## Ce Qui Doit Etre Modifie

- Utiliser la commande principale de collecte directe:

```powershell
python -m ict.cli archive collect-to-r2
```

- Ajouter un workflow GitHub Actions quotidien qui:
  - installe Python/Node;
  - lit les sources gratuites de `configs/live_sources.yaml`;
  - exclut Databento meme si une cle existe;
  - recupere les candles completes du jour UTC precedent;
  - exporte en Parquet compresse ZSTD;
  - chiffre en AES-256-GCM;
  - upload vers R2;
  - produit un log JSONL et un resume GitHub.

- Utiliser `max_workers=1` par defaut sur GitHub Actions. `dukascopy-node`
  est plus fiable en serie qu'en parallele; on privilegie la stabilite au gain
  de quelques minutes.

- Faire evoluer la page React `Data`:
  - afficher la couverture locale;
  - afficher la couverture R2;
  - proposer `Restore R2` / `Preparer donnees`;
  - garder `Databento` manuel uniquement.

- Faire evoluer Docker local:
  - au demarrage, restaurer depuis R2 seulement si les secrets R2 sont configures;
  - ne pas telecharger tout l'historique inutilement;
  - restaurer a la demande avant backtest si une periode manque.

- Mettre a jour la documentation architecture:
  - R2 devient l'archive remote officielle;
  - toute base remote SQL est hors chemin operationnel;
  - le schema draw.io devra montrer ce nouveau flux.

## Ce Qui Est Nouveau

- Nouveau module `ict.archive`.
- Nouveau format archive:

```text
Parquet -> compression ZSTD -> chiffrement AES-256-GCM -> R2
```

- Donnees archivees: candles stockees telles quelles dans le format explicite `canonical_market_candles_v1`.

- Nouvelle cle secrete:

```text
MARKET_ARCHIVE_KEY=<base64 de 32 bytes>
```

- Nouveau garde-fou de stockage:

```text
MARKET_ARCHIVE_MAX_BUCKET_GB=10
```

Le workflow quotidien passe aussi `--max-bucket-gb 10`. Avant chaque upload, le
collecteur calcule la taille reelle du bucket R2, soustrait les partitions qui
seraient remplacees, ajoute les nouvelles partitions, puis refuse l'upload si
la projection depasse la limite. Il ne supprime rien automatiquement en v1.

- Nouveaux secrets R2:

```text
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=...
R2_ENDPOINT_URL=...   # optionnel si R2_ACCOUNT_ID est fourni
```

- Nouveau layout R2:

```text
market-candles/
  source=dukascopy/
    symbol=EURUSD/
      timeframe=M1/
        year=2026/
          month=07/
            day=08/
              candles.parquet.zst.enc
              manifest.json
```

- Nouveau manifest par partition:
  - data_format;
  - symbole;
  - source;
  - source_symbol;
  - timeframe;
  - jour;
  - nombre de rows;
  - taille chiffree;
  - sha256 du fichier chiffre;
  - sha256 du Parquet avant chiffrement;
  - version de schema;
  - statut `complete`.

## Format Canonique Backtest

Le backtest ne consomme pas un format provider implicite. Il consomme la table locale `market_candles`.

Format archive v1: `canonical_market_candles_v1`.

Schema logique stocke tel quel dans l'archive:

```text
symbol_code
source_name
source_symbol
timeframe
time_open
open
high
low
close
tick_volume
real_volume
spread
quality_flags
metadata
```

La couche locale existe deja:

- `ict.data.ingest.ingest_market_data` recupere le provider local;
- `ict.data.normalizer.transformer_for_source` transforme les donnees brutes;
- `ict.data.quality.annotate_candle_quality` ajoute les flags qualite;
- `ict.data.quality.prepare_candles_for_storage` dedoublonne/nettoie avant stockage;
- `CandleRepository.rows_for_frame` convertit vers `market_candles`;
- `CandleRepository.upsert_candles` fait l'upsert idempotent.

Pour R2 direct, les providers live (`coinbase`, `kraken`, `dukascopy_node`) retournent deja des frames normalisees avec ces colonnes. L'archive R2 doit donc stocker ces frames normalisees, puis le restore doit reutiliser le chemin d'upsert local vers `market_candles`.

Regle importante: si on ajoute une source R2 qui sort du brut, elle doit passer par un transformer avant l'archive. On ne cree pas un deuxieme format parallele.

- Nouveaux controles gratuits:
  - limite de taille upload par run;
  - limite du nombre de partitions;
  - exclusion Databento automatique;
  - workflow qui skip si R2 n'est pas configure;
  - aucun cout Databento sans clic manuel dans l'app.

## Definition Du Succes

- Un run GitHub Actions quotidien archive les candles gratuites dans R2.
- R2 contient des manifests verifies par symbole/source/timeframe/jour.
- Le PC peut restaurer une periode manquante depuis R2 vers Postgres local.
- Un backtest utilise la DB locale apres restauration.
- Databento reste strictement manuel.
- Les limites gratuites sont visibles et protegees.

## Demarrage Local

Docker utilise `scripts/docker_api_entrypoint.sh` pour garder le lancement lisible:

1. migrations;
2. seed des assets/sources/strategies;
3. restore R2 optionnel;
4. demarrage FastAPI.

Variables utiles:

```text
MARKET_ARCHIVE_STARTUP_RESTORE_ENABLED=false
MARKET_ARCHIVE_STARTUP_DAYS=7
MARKET_ARCHIVE_STARTUP_SYMBOLS=
MARKET_ARCHIVE_STARTUP_SOURCES=
MARKET_ARCHIVE_STARTUP_MAX_DOWNLOAD_MB=1024
MARKET_ARCHIVE_STARTUP_RESTORE_FAIL_FAST=false
MARKET_ARCHIVE_CACHE_DIR=.cache/market_archive
```

Le restore de demarrage est desactive par defaut pour ne jamais bloquer
l'ouverture de l'API et du front. Pour preparer une vraie fenetre de backtest,
utiliser la page `Data` ou:

```powershell
.\scripts\dev.ps1 restore-r2
```
