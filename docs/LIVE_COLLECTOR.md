# Live Collector / Archive R2

Derniere mise a jour: 2026-07-09

Le chemin operationnel des donnees gratuites est maintenant:

```text
Providers gratuits -> GitHub Actions -> R2 archive chiffree -> PC local -> Postgres local -> backtests
```

Guide de reference actuel:

- [R2_ARCHIVE_GUIDE.md](R2_ARCHIVE_GUIDE.md)

## Architecture

- GitHub Actions est le runtime remote officiel v1.
- R2 stocke les candles gratuites en Parquet/ZSTD puis AES-256-GCM.
- La base locale Postgres reste la source canonique pour les backtests, dashboards et analyses.
- Cloudflare Worker, Oracle VM et Docker remote collector sont legacy et hors chemin operationnel.
- Databento est manuel uniquement; aucun workflow schedule ne le lance.

Ce n'est pas un stream broker-grade. C'est un batch idempotent: chaque run recupere une fenetre de candles completes, cree des partitions journalieres verifiees, puis les upload dans R2.

## Workflow GitHub Actions

- `.github/workflows/archive-to-r2-daily.yml`
  - Cron quotidien.
  - Couvre tous les actifs gratuits enabled dans `configs/live_sources.yaml`.
  - Exclut Databento.
  - Utilise `max_workers=1` par defaut pour stabiliser Dukascopy.
  - Applique `max_upload_mb` et `MARKET_ARCHIVE_MAX_BUCKET_GB=10`.

Chaque run produit:

- logs console groupes GitHub;
- artifact JSONL `collector-logs/archive-to-r2-daily.jsonl`;
- resume dans `GITHUB_STEP_SUMMARY`;
- partitions R2 avec manifest verifiable.

## Secrets GitHub

Secrets R2:

```text
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=...
R2_ENDPOINT_URL=...
MARKET_ARCHIVE_KEY=...
```

Secret manuel optionnel:

```text
DATABENTO_API_KEY=...
```

Si les valeurs sont deja dans `.env`, le helper local configure les secrets GitHub sans afficher les valeurs:

```powershell
.\scripts\configure_github_actions_secrets.ps1
```

Prerequis: GitHub CLI `gh` installe, `gh auth login` effectue, et repo GitHub remote configure.

## Sources Live V1

`configs/live_sources.yaml` contient les 40 actifs de l'univers:

- enabled free:
  - `dukascopy_node`: forex, `GER40`, `NAS100`, `XAGUSD`;
  - `coinbase` avec fallback `kraken`: cryptos stockees sous la source locale `binance`.
- manual paid:
  - `databento`: `MNQ`, disabled dans le scheduler, actif uniquement depuis le bouton Databento de l'app.
- pending:
  - actifs sans source cloud gratuite valide: `XAUUSD`, `SPX500`, `US30`, `UK100`, `FRA40`, `EU50`, `JPN225`.

Les pending doivent garder un `pending_reason` explicite tant qu'ils ne sont pas actives.

## Restore Local

Depuis le PC:

```powershell
python -m ict.cli archive restore-from-r2 --days 7
```

Ou depuis l'interface React:

- page `Data`;
- selectionner un ou plusieurs actifs;
- bouton `Preparer donnees R2`.

Le restore:

- lit les manifests R2;
- saute les partitions deja couvertes localement;
- utilise `MARKET_ARCHIVE_CACHE_DIR` pour les fichiers chiffres;
- dechiffre en memoire;
- upsert en base locale sans doublons.

Docker lance aussi un restore court au demarrage si les secrets R2 sont
configures. Les variables `MARKET_ARCHIVE_STARTUP_*` permettent de choisir la
fenetre, les symboles et le comportement en cas d'erreur.

## Monitoring

Commandes utiles:

```powershell
python -m ict.cli live sources --enabled-only
python -m ict.cli archive status --lookback-days 30
python -m ict.cli archive configured
python -m ict.cli archive collect-to-r2 --dry-run --max-bucket-gb 10
.\scripts\dev.ps1 restore-r2
.\scripts\dev.ps1 r2-status
```

Dans l'UI:

- page `Data`: couverture locale, couverture R2, pending reasons, usage bucket R2, restore R2 et Databento manuel.

## Legacy

- `collector/cloudflare`: conserve pour reference technique, hors chemin operationnel.
- Oracle VM: tentative annulee et ressources nettoyees; ne pas recreer sans verifier un cout strictement zero.
- Les anciennes decisions remote SQL sont conservees uniquement dans `docs/PROJECT_HISTORY.md`.
