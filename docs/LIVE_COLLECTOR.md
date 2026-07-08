# Live Collector

Le collecteur live officiel utilise maintenant **GitHub Actions -> Neon**. Il maintient un entrepot remote compact de candles M1, puis l'app locale peut rapatrier les donnees dans la base canonique du PC via la page `Data` ou `ict live sync --from-remote`.

## Architecture

- GitHub Actions est le runtime remote officiel v1.
- Neon Postgres stocke les candles compactes dans `live_market_candles`, plus `collector_runs`, `collector_source_state` et `collector_incidents`.
- La base locale Postgres reste la source canonique pour les backtests, dashboards et archives longues.
- Cloudflare Worker, Oracle VM et Docker remote collector sont legacy: ils ne sont plus le chemin operationnel.
- R2/object storage est reporte; v1 reste en Neon compact SQL uniquement.

Ce n'est pas un stream broker-grade. C'est un batch idempotent: chaque run recupere une fenetre de candles completes, applique un overlap, puis upsert par `(source_state_id, time_open)`.

## Workflows GitHub Actions

- `.github/workflows/live-collector-daily.yml`
  - Actif par defaut.
  - Cron quotidien `02:17 UTC`.
  - Couvre tous les actifs cloud-compatibles enabled dans `configs/live_sources.yaml`.

- `.github/workflows/live-collector-priority.yml`
  - Cron horaire `17 * * * *`, mais le job schedule ne tourne que si la variable repo `ENABLE_PRIORITY_COLLECTOR=true`.
  - Toujours lancable manuellement par `workflow_dispatch`.
  - Par defaut: actifs `priority <= 10`: `EURUSD`, `GER40`, `NAS100`, `BTCUSD`, `ETHUSD`.
  - Databento est exclu des workflows schedules; MNQ se lance uniquement depuis l'app quand l'utilisateur clique.

Chaque workflow produit:

- logs console groupes GitHub;
- artifact JSONL `collector-logs/*.jsonl`;
- resume dans `GITHUB_STEP_SUMMARY`;
- lignes DB dans `collector_runs`;
- incidents par actif si un provider echoue plusieurs fois.

## Secrets Et Variables GitHub

Secrets:

```text
LIVE_REMOTE_DATABASE_URL=postgresql://...
DATABENTO_API_KEY=...   # optionnel, uniquement pour les fetchs manuels MNQ/Databento
```

Variable optionnelle:

```text
ENABLE_PRIORITY_COLLECTOR=true
```

Le workflow daily fonctionne sans cette variable. Le workflow priority schedule reste inactif tant qu'elle n'est pas definie a `true`.

Si les valeurs sont deja dans `.env`, le helper local configure les secrets GitHub sans afficher les valeurs:

```powershell
.\scripts\configure_github_actions_secrets.ps1
```

Pour activer aussi le cron horaire priority:

```powershell
.\scripts\configure_github_actions_secrets.ps1 -EnablePriorityCollector
```

Prerequis: GitHub CLI `gh` installe, `gh auth login` effectue, et repo GitHub remote configure.

## Setup Remote

Le workflow bootstrappe Neon a chaque run de facon idempotente:

```bash
export DATABASE_URL="$LIVE_REMOTE_DATABASE_URL"
python -m ict.cli db upgrade
python -m ict.cli sources sync
python -m ict.cli symbols sync
python -m ict.cli live register-sources --remote-database-url "$LIVE_REMOTE_DATABASE_URL"
python -m ict.cli live collect-remote --emit-jsonl --log-path collector-logs/live-collector.jsonl
```

Pour tester sans toucher Neon ni les providers:

```bash
python -m ict.cli live collect-remote --dry-run --max-priority 10 --emit-jsonl --log-path collector-logs/dry-run.jsonl
```

## Sources Live V1

`configs/live_sources.yaml` contient les 40 actifs de l'univers:

- enabled cloud:
  - `dukascopy_node`: forex, `GER40`, `NAS100`, `XAGUSD`;
  - `coinbase` avec fallback `kraken`: cryptos stockees sous la source locale `binance`.
- manual paid:
  - `databento`: `MNQ`, disabled dans le scheduler, active uniquement depuis le bouton Databento de l'app.
- pending:
  - actifs MT5-only sans source cloud gratuite fiable: `XAUUSD`, `SPX500`, `US30`, `UK100`, `FRA40`, `EU50`, `JPN225`.

Les pending sont enregistres dans Neon comme sources desactivees pour etre visibles, mais ils ne sont pas executes par les workflows.

## Sync Locale

Depuis le PC:

```powershell
$env:LIVE_REMOTE_DATABASE_URL="postgresql://..."
python -m ict.cli live sync --from-remote --symbols BTCUSD,EURUSD --since 2026-07-01
```

Ou depuis l'interface React:

- page `Data`;
- selectionner un ou plusieurs actifs;
- bouton `Fetch Neon`.

La base locale reste la base de travail pour backtests et analyses.

## Monitoring

Commandes utiles:

```powershell
python -m ict.cli live status --remote-database-url $env:LIVE_REMOTE_DATABASE_URL
python -m ict.cli live incidents --remote-database-url $env:LIVE_REMOTE_DATABASE_URL
python -m ict.cli live sources --enabled-only
```

Dans l'UI:

- page `Data`: couverture locale, dernier candle Neon, fetch Neon pour les actifs gratuits, fetch Databento manuel pour MNQ;
- page `Live Collector` legacy Streamlit: runs, incidents, lag par source.

## Legacy

- `collector/cloudflare`: conserve pour reference technique, hors chemin operationnel.
- `deploy/collector/docker-compose.yml` et `scripts/live_collector_service.py`: conserve comme option legacy si un jour une VM gratuite fiable est disponible, mais ce n'est plus la cible.
- Oracle VM: tentative annulee et ressources nettoyees; ne pas recreer sans verifier un cout strictement zero.
