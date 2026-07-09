# Architecture Actuelle

Derniere mise a jour: 2026-07-09

Le schema d'architecture actuel est maintenu au format diagrams.net / draw.io:

- [ARCHITECTURE_CURRENT.drawio](ARCHITECTURE_CURRENT.drawio)

Pour l'ouvrir manuellement: aller sur https://app.diagrams.net, choisir `File > Open from Device`, puis selectionner `docs/ARCHITECTURE_CURRENT.drawio`.

## Version Actuelle

La plateforme locale tourne autour de `docker-compose.yml`:

- `ict_web`: app React + TypeScript + Vite, port `5173`, interface principale pour Run Lab, Strategy Builder, review trade, analytics et Data.
- `ict_api`: API FastAPI, port `8000`, lance migrations/seed au demarrage et expose backtests, strategies, review, analytics et restore data.
- `ict_postgres`: PostgreSQL 16, port `5432`, base locale canonique pour candles, strategies, runs, trades et metriques.
- `ict_adminer`: Adminer, port `8080`, inspection manuelle de la base locale.

La collecte remote ne tourne pas sur le PC local. Elle tourne via GitHub Actions:

- `archive-to-r2-daily.yml`: batch quotidien, sources gratuites enabled, upload direct dans R2.
- R2 stocke les candles en `canonical_market_candles_v1`, Parquet/ZSTD puis AES-256-GCM.
- Databento reste manuel et payant, principalement pour `MNQ`.
- Cloudflare Worker, Oracle VM et Docker remote collector sont legacy et hors chemin operationnel.

## Flux Principaux

- Navigateur -> React `ict_web` sur `5173`.
- React -> FastAPI `ict_api` sur `8000`.
- FastAPI -> PostgreSQL local via `DATABASE_URL`.
- GitHub Actions -> providers gratuits `dukascopy_node`, `coinbase`, fallback `kraken`.
- GitHub Actions -> R2 archive chiffree, avec budget bucket 10GB par defaut.
- React/FastAPI Data -> restore R2 des partitions manquantes -> Postgres local.
- Backtest -> verifie la couverture locale, restaure R2 si besoin, puis lit seulement Postgres local.
- Databento -> FastAPI/Data uniquement sur action manuelle avec garde-fou USD.

## Decisions Documentees

- La base locale Postgres reste la source canonique des backtests et dashboards.
- R2 est l'unique archive remote gratuite operationnelle.
- Aucun workflow schedule ne lance Databento.
- Le restore R2 utilise un cache local de fichiers chiffres et saute les partitions deja couvertes localement.
- React/FastAPI est la cible long terme pour l'outil principal.
- Streamlit reste temporaire pour les pages admin historiques, sans role dans le pipeline remote officiel.
- OANDA est retire du pipeline operationnel.
- Les secrets ne doivent jamais etre mis dans le schema; noter seulement les noms de variables d'environnement.

## Regle De Maintenance

Mettre a jour le schema quand l'un de ces elements change:

- ajout, suppression ou renommage d'un service/container;
- changement de port expose;
- changement du role d'un service;
- nouveau flux de donnees critique;
- nouvelle source de donnees operationnelle;
- changement de statut entre base locale, R2, GitHub Actions ou providers.

En cas de changement d'architecture, mettre a jour:

1. `docs/ARCHITECTURE_CURRENT.drawio`
2. `docs/ARCHITECTURE.md`
3. `docs/PROJECT_NOW.md` et `docs/PROJECT_THREAD.md` si le jalon projet change

## Commandes Utiles

```powershell
.\scripts\dev.ps1 up
.\scripts\dev.ps1 ps
.\scripts\dev.ps1 logs-api
.\scripts\dev.ps1 logs-web
.\scripts\dev.ps1 down
python -m ict.cli archive collect-to-r2 --dry-run --max-bucket-gb 10
python -m ict.cli archive restore-from-r2 --days 7
```
