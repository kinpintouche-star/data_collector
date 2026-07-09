# Historique Des Jalons - ICT Backtesting Platform

Derniere mise a jour: 2026-07-08

Ce fichier liste les jalons utiles du projet. Il ne sert pas a tracer les micro-changements, les essais ponctuels ou les tests de fonctions.

## Jalons Realises

### Vision V2: base canonique et transformateurs

La vision a ete recentree autour d'une seule base canonique locale et de transformateurs/adaptateurs de donnees. Les providers externes ne sont pas le modele metier; ils servent a alimenter des candles normalisees.

### Schema local et migrations

Mise en place de la structure Postgres locale, des migrations Alembic, des tables de candles, sources, symboles, datasets, runs de backtest, trades et metriques.

### Ingestion historique multi-sources

Ajout des providers et chemins d'ingestion pour CSV, Dukascopy, Binance public archives, MT5 selon terminal local, et Databento pour MNQ.

### Univers initial de 40 actifs

Creation de `configs/universe_default_40.yaml` avec 20 forex et 20 actifs mainstream: metaux, cryptos, indices, MNQ. Objectif: 6 mois de donnees M1 exploitables.

### Couverture historique 6 mois

Chargement local d'une grande partie de l'univers:

- Forex via Dukascopy.
- Crypto via Binance.
- GER40 et NAS100 via Dukascopy.
- MNQ via Databento.

Audit courant: 30/40 actifs atteignent la cible 180 jours M1.

### Gestion MNQ native vs proxy

Documentation de la difference entre MNQ natif CME et proxies Nasdaq CFD. Decision: ne pas melanger NAS100/MT5 proxy avec MNQ canonique.

### Decision Databento pour MNQ

Avec les credits Databento disponibles, MNQ natif reste la source retenue pour les backtests futures. Estimation verifiee pour 6 mois de candles M1 OHLCV: environ 0.6363 USD pour `MNQ.c.0` sur `GLBX.MDP3`.

### Collecteur live remote

Ajout du module live collector:

- tables `collector_runs`, `collector_source_state`, `collector_incidents`;
- table compacte remote `live_market_candles`;
- sync Neon -> local;
- sync local -> Neon;
- collector Python batchable;
- workflow GitHub Actions;
- Cloudflare Worker conserve comme helper secondaire.

### Provider OANDA live prepare

Ajout du provider live OANDA Practice pour preparer la collecte officielle gratuite de forex, metaux et indices CFD. Les entrees sont configurees mais desactivees tant que l'account id et le token practice n'ont pas confirme les instruments disponibles.

### OANDA retire du pipeline

Decision 2026-07-07: OANDA est retire du pipeline operationnel car le parcours compte demande un depot. La source reste desactivee dans la configuration pour que la base locale puisse etre synchronisee vers un etat inactif.

### Dashboard principal

Ajout d'une interface Streamlit pour explorer la plateforme:

- vue couverture marche;
- vue live collector;
- incidents/stale sources;
- bouton Neon -> Local;
- bases pour dashboards par strategie/bot.

### Gestion de donnees dans l'interface

Ajout d'une page `Data Management` pour voir jusqu'a quelle date chaque actif/source est disponible en local, comparer avec Neon, et lancer un rattrapage depuis Neon ou Databento selon la source configuree.

### Data Management v2: canaux de fetch

Ajout d'un canal de fetch unifie par actif/source, puis simplification: Neon devient le canal par defaut pour tous les actifs gratuits car le remote warehouse est alimente par GitHub Actions. Databento reste un canal manuel payant avec garde-fou de cout, principalement pour MNQ. Ajout d'une selection multi-actifs avec select all/clear et d'une section `API Usage` affichant limites pratiques, decoupage, couts et actifs concernes par API.

### Data Management React/FastAPI

Migration progressive de la gestion des donnees dans l'app React principale: nouvelle page `Data`, API FastAPI `/api/data/*`, couverture locale/Neon par actif-source, statut `jour complet OK` et `aujourd'hui present`, actions Neon/Databento, et jobs de fetch non bloquants avec resultats par actif.

### Protection contre les trous de donnees

Le backtest detecte les gaps, coupe les segments discontinus et enregistre des evenements/metriques de trous de donnees. L'objectif est d'eviter qu'une strategie garde un etat invalide a travers une periode manquante.

### Migration React/FastAPI Trading Lab

Decision 2026-07-07: Streamlit reste disponible pour les pages admin/data, mais l'environnement principal de revue trading migre vers une app React/FastAPI. La v1 cible un cockpit local avec charts H4, H1, M30, M15, M5, M1, markers de trade, fibo automatique, risk/reward zones et detection visible des gaps.

### React Trading Lab v2: single chart et analytics

Ajout d'un mode de review type TradingView avec un seul graphe visible par defaut, selection rapide de timeframe et grille 6 timeframes en option. Le fibo automatique est limite aux timeframes pertinentes au lieu d'etre dessine partout. Ajout d'une page `Analytics` dans React avec KPI, equity curve, PnL cumule, PnL mensuel, distribution RR, breakdowns de performance, funnel d'events et premiers diagnostics heuristiques.

### Integrite backtest et annotations trade

Correction de deux points critiques pour eviter les stats trompeuses: un trade encore ouvert en fin de run est desormais cloture en `RUN_END` au lieu d'etre exclu des resultats, et le CLI de backtest applique un tick size par actif quand la base locale n'en expose pas. La review React/FastAPI expose aussi des annotations structurees pour placer OB/FVG/OTE, swings M15 et niveaux CRT/objectif aux bons endroits du graphe.

### React Run Lab et analytics multi-actifs

Ajout d'un flux UI pour lancer des backtests depuis React: selection de strategie, periode, actifs/sources et suivi du job de lancement. Les runs lances ensemble partagent un `launch_id`, ce qui permet d'ouvrir une page analytics de groupe avec filtres par actif, bouton All et comparaison inter-actifs sur PnL, winrate, RR, expectancy et profit factor.

### Orchestration locale Docker Compose

Ajout d'un lancement local unifie via Docker Compose: Postgres, FastAPI, React/Vite et Adminer. Le compose execute les migrations et le seed de base avant l'API. Ajout d'un `Makefile` et de `scripts/dev.ps1` pour demarrer l'app, inspecter les services et consulter les logs sans devoir lancer chaque process a la main.

### Documentation architecture diagrams.net

Ajout d'un schema `docs/ARCHITECTURE_CURRENT.drawio` au format diagrams.net pour documenter la version actuelle: services Docker, ports, roles, base locale canonique, Streamlit legacy, Neon warehouse, GitHub Actions collector et providers externes. Ajout de `docs/ARCHITECTURE.md` comme notice de lecture et regle de maintenance.

### Playbook d'analyse strategie

Ajout de `docs/STRATEGY_ANALYSIS_PLAYBOOK.md` pour cadrer l'analyse et l'amelioration des strategies: hypotheses testables, metriques, segmentation, TP vers liquidite proche, SL structurel, alignement H1/M15/M1, confluence OB/FVG et garde-fous anti future leakage.

### Strategy Builder ICT/SMC v1

Ajout d'un Strategy Builder React/FastAPI avec stockage local `strategy_definitions`, catalogue de blocs, templates, validation, export YAML et lancement de backtest depuis Run Lab via `strategy_definition_id`. Ajout du moteur `StrategyBlueprintEngine`, qui orchestre les blocs CRT, biais, swings, jambe, fibo/OTE, retracement OB/FVG et ordre en conservant les events/metadata pour review et analytics.

### Strategies CRT H1 M1 et Immediate Rebalance

Recreation de la strategie historique sous le nom `CRT H1 M1` dans Strategy Builder. Ajout de l'indicateur mecanique `Immediate Rebalance` et d'une strategie experimentale multi-timeframes `Immediate Rebalance H1 M15 M1`, avec CRT H1, swing de protection M15, detection IR M1, stop loss sur l'origine IR et take profit vers objectif CRT/liquidite proche.

### Targets de liquidite et filtre tendance

Ajout des targets previous day/week/month high-low dans le moteur Strategy Builder pour les take profits. Ajout d'un bloc `filter.trend` qui calcule une tendance par timeframe a partir de la structure de swings, avec alignement possible sur le signal CRT ou sur la timeframe parente.

### Consolidation indicateurs ICT/SMC

Extension du moteur Strategy Builder avec targets sessions Asia/London/NY, equal highs/lows, BOS/MSS experimental et AMD range/sweep/displacement experimental. La review affiche maintenant les candidates de targets et les zones IR, et les analytics ajoutent un breakdown par source de target pour mesurer quelles zones de liquidite ameliorent ou degradent une strategie.

### Collecteur remote permanent

Correction d'architecture: le collecteur live ne doit pas tourner sur le PC local, car il s'arrete si le PC est eteint. Ajout d'un compose dedie `deploy/collector/docker-compose.yml` pour VM externe gratuite avec deux containers: `collector-live` pour les fenetres M1 recentes envoyees vers Neon, et `collector-repair` pour refetch les derniers jours UTC complets. Cloudflare est retire du chemin operationnel et son cron est desactive.

### Pivot GitHub Actions Collector

Decision 2026-07-08: Oracle/VM Docker n'est pas retenu car l'essai Always Free indiquait un cout potentiel de boot volume. Les ressources Oracle creees pendant l'essai ont ete nettoyees. Le chemin officiel devient GitHub Actions -> Neon, avec workflow daily complet, workflow priority free-safe, logs JSONL, bootstrap remote idempotent et sources gratuites `dukascopy_node` et `coinbase/kraken`. Databento est conserve comme canal manuel payant pour MNQ, jamais comme collecte schedulee.

### Retention Neon prudente

Ajout des commandes `ict live storage` et `ict live prune-remote`. Mesure reelle du 2026-07-08: 2 264 673 candles crypto occupent environ 472.8 MB dans `live_market_candles`, proche du quota Neon Free. La retention Neon cible passe donc a 30 jours; le prune est manuel, en dry-run par defaut, et verifie que les candles candidates existent deja dans la base locale avant suppression.

### Pivot R2 sans base remote SQL

Decision 2026-07-09: Neon sort du chemin operationnel. Le pipeline officiel devient `providers gratuits -> GitHub Actions -> R2 archive chiffree -> PC cache -> Postgres local -> backtests`. Les workflows GitHub Actions SQL remote sont retires, le helper de secrets ne configure plus de base remote, la page Data React ne propose plus que R2 pour les actifs gratuits et Databento en manuel pour MNQ. Les anciennes decisions Neon restent dans cet historique uniquement.

## Jalons A Venir

### Extension live vers 40 actifs

Etendre la couverture remote au-dela des 32 actifs gratuits scheduled en testant les 7 `pending_cloud_source` sur des alias Dukascopy ou d'autres sources gratuites R2-compatibles. MNQ reste Databento manuel.

### Interface strategie parametrable

Construire une interface permettant de choisir une strategie, d'ajuster ses criteres, puis de lancer des backtests sans modifier le code.

### Systeme de blocs/regles

Introduire un modele generique de primitives de strategie, avec parametres et assemblage progressif vers une interface type noeuds.

### Graphique de revue des trades

Etendre la vue type TradingView avec annotations persistantes, fibo manuel draggable et comparaison de trades similaires.

### Diagnostic strategie avance

Transformer les diagnostics heuristiques en analyses plus fortes: comparaison de regimes, clustering de trades similaires, sensibilite aux parametres et eventuellement modele ML lorsque le volume de trades sera suffisant.
