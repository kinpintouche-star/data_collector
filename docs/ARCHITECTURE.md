# Architecture Actuelle

Derniere mise a jour: 2026-07-08

Le schema d'architecture actuel est maintenu au format diagrams.net / draw.io:

- [ARCHITECTURE_CURRENT.drawio](ARCHITECTURE_CURRENT.drawio)

Pour l'ouvrir manuellement: aller sur https://app.diagrams.net, choisir `File > Open from Device`, puis selectionner `docs/ARCHITECTURE_CURRENT.drawio`.

## Version Actuelle

La plateforme locale tourne autour de `docker-compose.yml`:

- `ict_web`: app React + TypeScript + Vite, port `5173`, interface principale cible pour Run Lab, Strategy Builder, review trade et analytics.
- `ict_api`: API FastAPI, port `8000`, lance les migrations/seed au demarrage, expose les endpoints backtest, Strategy Builder, review, analytics et data.
- `ict_postgres`: PostgreSQL 16, port `5432`, base locale canonique pour candles, definitions de strategies, runs, trades, metriques et monitoring collector.
- `ict_adminer`: Adminer, port `8080`, inspection manuelle de la base locale.

La collecte live n'est pas un container du PC local. Elle tourne officiellement via GitHub Actions:

- `live-collector-daily.yml`: batch quotidien complet a `02:17 UTC`, ecrit vers Neon.
- `live-collector-priority.yml`: batch prioritaire free-safe, horaire seulement si `ENABLE_PRIORITY_COLLECTOR=true`, sinon manuel.
- Les anciens chemins Oracle VM / Docker remote collector / Cloudflare Worker sont legacy et hors chemin operationnel.

Streamlit reste disponible hors compose principal pour les pages admin/data historiques, notamment Data Management, coverage, Live Collector et synchronisation Neon -> local.

## Flux Principaux

- Navigateur -> React `ict_web` sur `5173`.
- React -> FastAPI `ict_api` sur `8000`.
- FastAPI -> PostgreSQL local via `DATABASE_URL`.
- Strategy Builder -> `strategy_definitions` pour drafts/versions, puis backtest via `strategy_definition_id`.
- Adminer -> PostgreSQL local pour inspection.
- GitHub Actions collector -> Neon pour les batchs live et reparations journalieres.
- FastAPI/CLI -> Neon pour synchroniser le warehouse remote vers la base locale.
- Providers externes -> GitHub Actions pour Dukascopy et Coinbase/Kraken vers Neon; FastAPI/Data Management pour Neon -> local et Databento manuel; MT5 reste optionnel local.

## Decisions Documentees

- La base locale Postgres reste la source canonique pour les backtests et dashboards.
- Neon sert d'entrepot/buffer remote recent, avec cible 30 jours dans le projet Free actuel; il ne remplace pas la base locale 180 jours+.
- La retention Neon se surveille via `ict live storage`; le prune remote est manuel et verifie la presence locale des candles par defaut.
- React/FastAPI est la cible long terme pour l'outil principal.
- Strategy Builder v1 utilise des blocs ordonnes ICT/SMC; le canvas type noeuds viendra apres stabilisation du pipeline.
- Streamlit reste temporaire pour les pages data/admin tant qu'elles ne sont pas migrees.
- OANDA est retire du pipeline operationnel.
- Cloudflare Worker, Oracle VM et Docker remote collector sont retires du chemin de collecte operationnel et conserves en legacy.
- GitHub Actions est le runtime officiel pour alimenter Neon.
- Les secrets ne doivent jamais etre mis dans le schema; noter seulement les noms de variables d'environnement.

## Regle De Maintenance

Mettre a jour le schema quand l'un de ces elements change:

- ajout, suppression ou renommage d'un service/container;
- changement de port expose;
- changement du role d'un service;
- nouveau flux de donnees critique;
- nouvelle source de donnees operationnelle;
- changement de statut entre base locale, Neon, Cloudflare, GitHub Actions ou providers.

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
```
