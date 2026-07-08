# Guide Archive R2 - Donnees Marche

Derniere mise a jour: 2026-07-08

## Objectif Maintenant

Le chemin cible des donnees gratuites devient:

```text
Providers gratuits -> GitHub Actions -> R2 archive chiffree -> PC local -> Postgres local -> backtests
```

Neon n'est plus le coeur du pipeline live. Il peut rester temporairement pour transition, diagnostic ou rattrapage recent, mais le stockage durable gratuit vise maintenant **Cloudflare R2**.

Le but n'est pas de garder 6 mois dans Neon. Le but est d'archiver chaque jour les candles recuperees par GitHub Actions directement dans R2, puis de restaurer localement uniquement les partitions utiles quand on veut lancer un backtest.

Contraintes non negociables:

- gratuit d'abord;
- Databento jamais automatique;
- pas de source payante dans les workflows schedules;
- la base locale Postgres reste la base canonique pour backtester;
- R2 stocke des fichiers compresses puis chiffres;
- le backtest ne lit pas directement R2, il lit localement.

## Ce Qui Doit Etre Modifie

- Remplacer le chemin officiel `GitHub Actions -> Neon` par `GitHub Actions -> R2`.
- Ajouter une commande principale de collecte directe:

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

- Faire evoluer la page React `Data`:
  - afficher la couverture locale;
  - afficher la couverture R2;
  - garder Neon comme bouton secondaire si configure;
  - ajouter `Restore R2`;
  - garder `Databento` manuel uniquement.

- Faire evoluer Docker local:
  - au demarrage, restaurer depuis R2 seulement si les secrets R2 sont configures;
  - ne pas telecharger tout l'historique inutilement;
  - restaurer a la demande avant backtest si une periode manque.

- Mettre a jour la documentation architecture:
  - R2 devient l'archive remote officielle;
  - Neon devient legacy/transition;
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

## Statut De Neon

Neon n'est pas supprime du projet immediatement.

Statut cible:

- `legacy/transition`;
- utile pour verifier l'ancien pipeline;
- utile comme fallback recent si on veut;
- pas source principale de stockage;
- pas archive 6 mois.

Regle simple: si une donnee gratuite peut aller directement dans R2, elle ne doit pas faire un detour obligatoire par Neon.

## Definition Du Succes

- Un run GitHub Actions quotidien peut archiver les candles gratuites dans R2 sans Neon.
- R2 contient des manifests verifies par symbole/source/timeframe/jour.
- Le PC peut restaurer une periode manquante depuis R2 vers Postgres local.
- Un backtest utilise la DB locale apres restauration.
- Databento reste strictement manuel.
- Les limites gratuites sont visibles et protegees.
