# Fil Projet - ICT Backtesting Platform

Derniere mise a jour: 2026-07-08

## But Principal

Le projet principal est de creer un environnement fiable pour tester des strategies en backtest.

Construire une plateforme locale de backtest et d'analyse de strategies de trading ICT/CRT, avec une base canonique sur le PC, une interface claire, et assez de donnees M1 propres pour comprendre pourquoi une strategie marche ou ne marche pas.

Le coeur du projet n'est pas seulement de lancer des backtests. Il faut pouvoir inspecter les trades sur graphique, comparer les actifs, voir les zones de declenchement, reperer les trous de donnees, et faire evoluer les strategies par blocs/regles parametrables.

## Architecture Cible

- Base locale Postgres: source principale et canonique pour les backtests et dashboards.
- R2 remote: archive durable cible pour les candles gratuites, stockees en Parquet/ZSTD puis chiffrees.
- Neon remote: legacy/transition, utile comme fallback recent mais plus comme chemin principal.
- Collecteurs: GitHub Actions doivent archiver directement les sources gratuites vers R2; Data Management local restaure R2 ou lance Databento manuellement.
- Donnees canonisees: stockage idempotent par symbole, source, timeframe et `time_open`.
- Interface principale cible: React/FastAPI pour le cockpit trading, la review des backtests, les dashboards analytiques de strategie et la gestion des donnees. Streamlit reste en parallele pour les pages admin/data pas encore migrees.
- Orchestration locale cible: `docker compose` pour lancer Postgres, FastAPI, React/Vite et Adminer ensemble; `scripts/dev.ps1` et `Makefile` exposent les commandes courantes.
- Schema architecture: `docs/ARCHITECTURE_CURRENT.drawio` est la version actuelle diagrams.net; le maintenir avec `docs/ARCHITECTURE.md` quand un service, container, port, provider ou flux critique change.

## Sources De Donnees

Priorite actuelle:

- Forex: Dukascopy pour historique gratuit M1; GitHub Actions utilise `dukascopy_node` pour archiver les actifs compatibles vers R2.
- Crypto: Binance archives pour historique; Coinbase avec fallback Kraken pour live GitHub Actions.
- Indices CFD: Dukascopy pour GER40/NAS100 vers R2; Databento reste reserve au MNQ manuel.
- MNQ natif: Databento/CME/broker futures seulement. NAS100 peut servir de proxy de recherche, mais ne doit pas etre stocke comme MNQ canonique.
- MT5: source locale utile si le terminal expose les symboles, mais pas fiable pour un collecteur cloud gratuit.
- OANDA: retire du pipeline operationnel, car le parcours compte demande un depot.

## Regles De Qualite

- Ne jamais melanger un proxy avec un actif natif sans le nommer explicitement.
- Garder les trous de donnees visibles; ne pas les masquer par interpolation silencieuse.
- Les backtests doivent couper/reprendre sur segments continus pour eviter de porter un signal ou une position a travers un trou.
- Les trades doivent toujours avoir une issue explicite dans les resultats: TP, SL, sortie controlee ou `RUN_END`; on ne doit pas exclure les trades ouverts car cela gonfle artificiellement le winrate.
- Les SL/TP doivent rester visibles dans la review et dans les exports de trades, avec un tick size coherent par actif.
- Toute source payante ou a credits doit etre separee des sources gratuites par configuration.

## Direction Strategie

Court terme:

- Finaliser une couverture live gratuite la plus large possible.
- Utiliser GitHub Actions comme collecteur remote officiel vers R2, sans passage obligatoire par Neon.
- Piloter les rattrapages depuis la page React `Data` via R2 par defaut, Neon en fallback secondaire, et Databento uniquement sur action manuelle.
- Garder Neon seulement comme transition/fallback recent, pas comme archive.
- Utiliser le React Trading Lab pour inspecter les trades avec timeframes H4/H1/M30/M15/M5/M1, mode single chart par defaut, grille multi-timeframes optionnelle, fibo automatique limite aux timeframes pertinentes, events, entree/sortie, SL/TP et gaps.
- Placer les elements de decision au bon endroit: OB/FVG/OTE comme zones temporelles, swings sur leurs candles de validation/structure, CRT et objectif comme niveaux dedies.
- Lancer les backtests depuis React via Run Lab: strategie, periode, actif unique ou panier multi-actifs; chaque panier multi-actifs devient un groupe de runs comparable.
- Creer des strategies depuis React via Strategy Builder: blocs ICT/SMC ordonnes par timeframe, drafts en DB, validation, export YAML et backtests via `strategy_definition_id`.
- Repartir des deux drafts actuels: `CRT H1 M1` pour la strategie historique, et `Immediate Rebalance H1 M15 M1` pour tester le pattern IR H1/M15/M1.
- Tester les nouveaux blocs experimentaux `BOS/MSS`, `detect.session_range` et `detect.amd_phase` separement avant de les valider comme composants de reference.
- Comparer les sources de take profit dans Analytics via `target_source`: objectif CRT, PD/PW/PM, Asia/London/NY, equal highs/lows et swings H1/M15.
- Utiliser la page Analytics React pour lire rapidement winrate, RR, equity, PnL mensuel, breakdowns et premiers diagnostics de faiblesses par strategie/run.
- Dans Analytics, permettre de passer de All a un ou plusieurs actifs selectionnes pour lire soit le dashboard d'un actif, soit un dashboard comparatif multi-actifs.
- Lancer l'environnement local complet via `.\scripts\dev.ps1 up` ou `make up` pour eviter les problemes de process detaches API/Web sous Windows.
- Utiliser `docs/STRATEGY_ANALYSIS_PLAYBOOK.md` pour transformer les idees d'amelioration en hypotheses testables avant de les coder.

Moyen terme:

- Construire une interface de strategie parametrable.
- Introduire des primitives generiques: sessions, ranges, liquidity sweeps, PD arrays, BOS/MSS, AMD experimental, CRT, confirmations, invalidations, risque, exits.
- Ajouter les primitives issues du playbook strategie: HTF bias H1/M15, niveaux de liquidite, target extension bornee, stop structurel OTE/FVG/OB et confluence HTF.
- Consolider la primitive Immediate Rebalance: mesurer winrate, RR, drawdown, extension post-IR et comportement par actif/session avant d'en faire un bloc valide.
- Permettre de tester des variantes de regles sans reecrire un bot complet.
- Stabiliser le moteur `StrategyBlueprintEngine`, puis migrer progressivement les strategies consolidees vers des blocs reutilisables.

Long terme:

- Evoluer vers un systeme de type blocs/noeuds pour assembler des algorithmes, proche de l'esprit Blueprint/Unreal.
- Ajouter des dashboards par bot: performance, regimes de marche, erreurs recurrentes, trades sur graphique, sensibilite aux parametres, puis analyses avancees/ML quand le dataset de trades est assez grand.
