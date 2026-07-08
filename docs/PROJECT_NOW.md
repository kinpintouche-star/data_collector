# Etat Courant - ICT Backtesting Platform

Derniere mise a jour: 2026-07-08

## Position Actuelle

Priorite de reference: le projet principal est de creer un environnement fiable pour tester des strategies en backtest.

Le projet a une base locale canonique, une base Neon remote pour le live/warehouse, un dashboard Streamlit, une interface React/FastAPI principale, des providers historiques, un collecteur live Python/GitHub Actions officiel, et une protection backtest contre les trous de candles.

Le point ouvert principal est la couverture live/gratuite des actifs non-crypto au-dela de Dukascopy. Les cryptos fonctionnent deja en live/batch via sources publiques. OANDA est retire du pipeline operationnel car le parcours compte demande un depot.

La page `Data` React/FastAPI pilote les donnees: couverture locale par actif/source, informations d'usage API, dernier candle Neon, fetch simplifie par canal `Neon` ou `Databento`, selection multi-actifs et jobs de fetch non bloquants. Streamlit garde temporairement l'ancien `Data Management` comme fallback.

Nouveau jalon UI: le cockpit `React Trading Lab` est ajoute en parallele de Streamlit. Il cible la review d'un trade avec charts H4, H1, M30, M15, M5, M1, events de setup, fibo automatique, entree/sortie, SL/TP et alertes de gaps. Le mode par defaut est maintenant un seul graphe avec selection de timeframe, avec une grille 6 timeframes disponible en option.

Nouveau jalon analytics: l'app React integre une page `Analytics` par run avec KPI strategie, equity curve, PnL cumule, PnL mensuel, distribution RR, breakdowns par direction/PD/session/heure/exit, funnel d'events et diagnostics heuristiques v1 pour reperer les segments faibles.

Nouveau jalon integrite backtest/review: les trades encore ouverts en fin de run sont maintenant clotures en `RUN_END` au lieu d'etre ignores, les backtests locaux utilisent un tick size adapte a l'actif quand la base n'en expose pas, et la review React recoit des annotations structurees: zones OB/FVG/OTE temporelles, swings places sur leurs candles et niveaux CRT/objectif.

Nouveau jalon Run Lab: l'app React/FastAPI permet de preparer un backtest depuis l'interface avec selection de strategie, periode et un ou plusieurs actifs/sources. Les lancements multi-actifs sont regroupes par `launch_id`, puis la page `Analytics` peut analyser un groupe de runs avec filtre par actif, bouton All et comparaison inter-actifs.

Nouveau jalon orchestration locale: lancement simplifie via Docker Compose avec services `postgres`, `api`, `web` et `adminer`, plus `Makefile` et `scripts/dev.ps1` pour demarrer, voir les logs et afficher les URLs utiles. Le collecteur live n'est pas dans ce compose local, car il doit continuer de tourner quand le PC est eteint.

Nouveau jalon collecteur remote GitHub Actions: Oracle/VM Docker/Cloudflare sont passes en legacy. Le chemin officiel est maintenant GitHub Actions -> Neon, avec workflow daily complet, workflow priority free-safe, logs JSONL, resume GitHub Actions et metadata GitHub dans `collector_runs`.

Nouveau jalon documentation architecture: ajout d'un schema diagrams.net versionne dans `docs/ARCHITECTURE_CURRENT.drawio` et d'une note `docs/ARCHITECTURE.md`. Le schema decrit les containers, ports, roles, flux locaux, flux Neon/GitHub Actions/providers, et doit etre mis a jour quand l'architecture change.

Nouveau jalon recherche strategie: ajout de `docs/STRATEGY_ANALYSIS_PLAYBOOK.md`, un cadre pour transformer les idees de strategie en hypotheses testables. Il formalise les pistes TP vers liquidite proche, SL structurel sous OTE/FVG/OB, alignement H1/M15/M1, double consolidation OB/FVG et RR minimum, avec garde-fous contre le future leakage.

Nouveau jalon Strategy Builder: ajout d'une v1 React/FastAPI pour creer des strategies ICT/SMC par blocs ordonnes. Les definitions sont stockees en base locale dans `strategy_definitions`, validables/exportables en YAML, et le Run Lab peut lancer un backtest via `strategy_definition_id`. Le nouveau `StrategyBlueprintEngine` orchestre CRT, biais, swings, jambe, fibo/OTE, retracement OB/FVG et ordre sans remplacer le moteur historique.

Nouveau jalon strategies v1: la strategie historique est recreee comme draft `CRT H1 M1` dans Strategy Builder. Ajout d'une primitive `Immediate Rebalance` et d'une strategie experimentale `Immediate Rebalance H1 M15 M1`: CRT H1, swing de protection M15, detection IR M1, SL sur l'origine IR et TP CRT/liquidite proche.

Nouveau jalon indicateurs strategie: `compute.target` gere maintenant previous day/week/month high-low, sessions Asia/London/NY, equal highs/lows et swings H1/M15 comme candidates de take profit, sans future leakage. Ajout des blocs experimentaux `trigger.bos_mss`, `detect.session_range` et `detect.amd_phase`, tout en conservant `filter.trend` par structure de swings.

Nouveau jalon review/analytics strategie: la review affiche les targets candidates et les zones IR; les analytics ajoutent un breakdown par `target_source` pour comparer PD/PW/PM, sessions, equal highs/lows, swings et objectif CRT.

## Donnees Locales M1

Audit local 180 jours:

- 40 actifs configures.
- 30 actifs avec couverture cible OK.
- Forex: majoritairement OK via Dukascopy.
- Crypto: OK via Binance.
- GER40 et NAS100: OK via Dukascopy.
- MNQ: OK via Databento sur la fenetre chargee, mais ce n'est pas une source gratuite permanente.
- XAUUSD, XAGUSD, SPX500, US30, UK100, FRA40, EU50, JPN225: configures mais pas encore remplis localement.

## Live Collector

En place:

- Tables de monitoring collector.
- Sync Neon -> local depuis l'interface.
- Sync remote -> local via CLI.
- Sync local -> remote pour seed/retention.
- GitHub Actions daily officiel pour alimenter Neon.
- GitHub Actions priority free-safe, actif en schedule seulement si `ENABLE_PRIORITY_COLLECTOR=true`.
- Docker remote collector, Oracle VM et Cloudflare Worker conserves en legacy, hors chemin operationnel.
- Logs JSONL et resume GitHub Actions par run.
- Page React `Data` avec action de fetch des donnees manquantes depuis Neon, ou Databento uniquement en manuel.
- Section `API Usage` dans `Data`: limites pratiques, decoupage courant, cout/garde-fou, actifs concernes par canal.
- Commandes `ict live storage` et `ict live prune-remote` pour surveiller Neon et supprimer manuellement les candles anciennes seulement apres verification locale. La retention Neon cible passe a 30 jours; la retention 180 jours reste locale tant qu'une archive objet gratuite n'est pas ajoutee.

Limite actuelle:

- `configs/live_sources.yaml` couvre les 40 actifs: 32 cloud-compatibles gratuits enabled, MNQ/Databento en manuel payant, 7 MT5-only en `pending_cloud_source`.
- Les actifs cloud enabled sont collectes vers Neon via Coinbase/Kraken ou Dukascopy-node. Databento n'est jamais lance par le scheduler.
- Les actifs pending restent rattrapes localement via MT5/Data Management tant qu'une source cloud gratuite fiable n'est pas trouvee.

## Decision MNQ

MNQ ne doit pas etre calcule comme s'il etait identique a NAS100.

On peut utiliser NAS100/US100 comme proxy de recherche pour generer des signaux ou comparer des regimes, mais:

- les prix ne sont pas les prix du futures MNQ;
- les horaires, spreads, roll, gaps et bases peuvent differer;
- les resultats ne doivent pas etre presentes comme un backtest MNQ natif;
- les fills, stops et targets doivent rester sur donnees MNQ natives si l'objectif est d'evaluer une strategie futures.

Statut: garder `MNQ` natif via Databento/CME/broker; ajouter si besoin `NAS100` ou `MNQ_PROXY_NAS100` comme proxy clairement separe.

Decision 2026-07-07: utiliser Databento pour MNQ tant que les credits disponibles couvrent le besoin. Estimation verifiee via `metadata.get_cost` pour `MNQ.c.0`, dataset `GLBX.MDP3`, schema `ohlcv-1m`, du 2026-01-01 au 2026-07-01: environ 0.6363 USD. Ce chiffre vaut pour des candles M1 OHLCV, pas pour du tick/MBO/order book.

## Canaux De Fetch Data Management

- `Neon`: canal par defaut pour tous les actifs non Databento; sync depuis l'entrepot remote vers la base locale canonique.
- `Databento`: canal manuel pour `source_type=databento`, principalement MNQ; garde-fou `Max Databento USD` avant telechargement. Reference MNQ M1 2026-01-01 -> 2026-07-01: environ 0.6363 USD.

## OANDA

Decision 2026-07-07: OANDA est retire du pipeline operationnel. La source est gardee inactive dans `configs/sources.yaml` pour que `ict sources sync` puisse desactiver l'ancien etat local si besoin.

## Questions Ouvertes

- Pour MNQ, garde-t-on Databento tant que les credits existent, ou ajoute-t-on un proxy NAS100 separe pour les tests gratuits ?
- Ajouter une archive remote gratuite compressee type R2/Parquet ou une strategie multi-projets Neon si l'on veut vraiment 180 jours remote pour tout l'univers.
- Quelles pages Streamlit doivent etre migrees en priorite dans React apres la review trade et les analytics de strategie ?
- Quels diagnostics strategie doivent devenir des analyses avancees ou un modele ML quand le volume de trades sera suffisant ?
- Ajouter une configuration explicite de tick size par symbole dans `configs/symbols.yaml` pour remplacer les fallbacks de securite.
- Ajouter une gestion persistante des jobs de backtest si l'on veut suivre un lancement meme apres redemarrage de l'API locale.
- Garder le schema `docs/ARCHITECTURE_CURRENT.drawio` synchronise avec les prochains changements d'architecture.
- Transformer le playbook strategie en primitives backtest configurables: HTF bias, niveaux de liquidite, SL structurel, target extension et confluence H1/M15.
- Faire evoluer Strategy Builder v1 vers un canvas type noeuds quand le pipeline par blocs sera stable.
- Tester `Immediate Rebalance H1 M15 M1` sur plusieurs actifs/periodes, puis decider si l'extension post-IR devient un filtre strict, une metrique post-entree ou un module de sortie.
- Valider les blocs experimentaux `BOS/MSS`, `session_range`, `equal highs/lows` et `AMD` sur EURUSD, GER40, MNQ et BTCUSD avant de les utiliser dans une strategie de reference.
