# Strategy Analysis Playbook

Derniere mise a jour: 2026-07-07

Objectif: donner une methode stable pour analyser une strategie, proposer de meilleures variantes, puis les tester proprement dans le backtester.

Le but n'est pas de declarer qu'une idee est meilleure parce qu'elle semble logique sur quelques trades. Chaque idee doit devenir une hypothese testable, comparer ses resultats au baseline, puis etre acceptee ou rejetee avec des metriques.

## Principe Central

Toute proposition de strategie suit ce cycle:

1. Observer un probleme dans les runs ou dans la review trade.
2. Formuler une hypothese explicite.
3. Transformer l'hypothese en regle deterministe.
4. Lancer baseline et variante sur les memes actifs, memes dates et memes donnees.
5. Comparer expectancy, RR realise, profit factor, drawdown, nombre de trades, winrate et stabilite par segment.
6. Garder la variante seulement si elle ameliore le profil global sans sur-optimiser un petit echantillon.

Regle dure: ne jamais utiliser une information qui n'etait pas connue au moment de l'entree. Les niveaux daily/monthly/session doivent etre des niveaux deja formes ou precedents, pas des highs/lows futurs.

## Checklist D'Analyse D'Un Run

Avant de proposer une variante, verifier:

- Les donnees M1 sont continues ou les gaps sont explicitement exclus par segments.
- Les trades ouverts sont clotures avec une issue explicite: TP, SL, sortie controlee ou `RUN_END`.
- Les SL/TP sont bien visibles dans la review.
- Le tick size de l'actif est coherent.
- Le run contient assez de trades pour juger une modification.
- Les resultats sont regardes par actif, direction, session, heure, PD array, type de sortie et mois.
- Le winrate n'est jamais lu seul: il doit etre croise avec expectancy, RR moyen/median, profit factor et drawdown.

## Metriques A Regarder

Metriques prioritaires:

- `expectancy_R`: gain moyen attendu par trade, en R.
- `profit_factor`: gains bruts / pertes brutes.
- `max_drawdown_R`: drawdown maximum en R.
- `avg_rr` et `median_rr`: RR realise.
- `winrate`: utile seulement avec RR et expectancy.
- `trade_count`: une variante qui gagne car elle ne prend presque plus de trades doit etre traitee avec prudence.
- `exit_mix`: repartition TP / SL / RUN_END / autre.
- `monthly_pnl_R`: robustesse mois par mois.

Segments prioritaires:

- actif;
- source de donnees;
- direction long/short;
- session London/New York/Asia;
- heure d'entree;
- type de PD array: OB, FVG, OB_OR_FVG;
- distance entree -> SL;
- distance entree -> target;
- presence d'un biais H1/M15 aligne;
- proximite d'une liquidite HTF ou session.

## Hypotheses Candidates

### 1. Extension Du Take Profit Vers La Liquidite

Hypothese: le TP CRT initial capte parfois trop peu le mouvement quand une liquidite claire est proche du target. Etirer le TP vers cette liquidite peut ameliorer le RR sans rendre le trade trop ambitieux.

Niveaux candidats:

- previous day high / previous day low;
- previous week high / previous week low;
- previous month high / previous month low;
- Asian high / Asian low deja formes;
- London high / London low deja formes;
- equal highs / equal lows confirmes avant l'entree;
- swing H1/M15 recent valide avant l'entree.

Regle v1 proposee:

- Calculer le TP initial CRT.
- Chercher le niveau de liquidite le plus proche dans le sens du trade.
- Garder ce niveau seulement s'il est au-dela du TP initial.
- Rejeter le niveau si le RR etendu depasse `2.0 * RR_initial`.
- Rejeter le niveau si le RR initial devient inferieur au minimum accepte.
- Variante conservative: prendre partiel au TP CRT et garder un runner vers la liquidite.
- Variante simple v1: remplacer le TP par la liquidite seulement si elle reste proche du TP CRT.

Parametres possibles:

```yaml
target_model:
  mode: crt_then_nearest_liquidity
  max_rr_multiplier_from_initial: 2.0
  min_initial_rr: 2.0
  allow_partial_at_crt: false
  liquidity_levels:
    - previous_day_high_low
    - previous_week_high_low
    - previous_month_high_low
    - asian_high_low
    - london_high_low
    - h1_m15_swings
```

Questions a mesurer:

- Est-ce que l'extension augmente expectancy ou seulement le gain moyen des winners ?
- Est-ce que le winrate baisse trop ?
- Quels actifs supportent bien l'extension ?
- Les trades qui echouent apres extension auraient-ils au moins touche le TP CRT ?

### 2. Stop Loss Structurel Sous OTE / FVG / OB

Hypothese: un SL place sous la structure qui a consolide le trade protege mieux l'idee de setup qu'un SL trop mecanique.

Regle bullish:

- Placer le SL sous le plus bas pertinent de la zone OTE, du FVG ou de l'order block selectionne.
- Ajouter un buffer en ticks ou en fraction d'ATR.
- Si le SL devient trop large et casse le RR minimum, ne pas prendre le trade.

Regle bearish:

- Placer le SL au-dessus du plus haut pertinent de la zone OTE, du FVG ou de l'order block selectionne.
- Ajouter un buffer en ticks ou en fraction d'ATR.
- Si le SL devient trop large et casse le RR minimum, ne pas prendre le trade.

Parametres possibles:

```yaml
stop_model:
  mode: structural_pd_array
  candidates:
    - ote_boundary
    - selected_fvg
    - selected_order_block
    - m1_swing
  buffer_ticks: 2
  atr_buffer_multiplier: 0.0
  min_rr_after_stop: 2.0
  max_sl_distance_atr: 2.5
```

Questions a mesurer:

- La variante reduit-elle les SL pris avant le mouvement attendu ?
- Augmente-t-elle trop la distance de stop ?
- Le profit factor augmente-t-il ou le risque moyen explose-t-il ?

### 3. Alignement H1 / M15 / M1

Hypothese: la strategie M1 est plus solide si elle ne prend que les setups dans le sens d'un biais H1 et M15 clair.

Regle v1 proposee:

- Determiner un biais H1: bullish, bearish ou neutral.
- Determiner un biais M15: bullish, bearish ou neutral.
- Autoriser un long M1 seulement si H1 et M15 sont bullish.
- Autoriser un short M1 seulement si H1 et M15 sont bearish.
- Si un des deux est neutral, tester deux variantes: strict reject ou M15-only.

Methodes possibles de biais:

- structure de swings: HH/HL pour bullish, LL/LH pour bearish;
- close au-dessus/en-dessous d'un swing valide;
- position du prix par rapport au range precedent;
- presence d'un OB/FVG HTF dans le sens du trade.

Parametres possibles:

```yaml
htf_bias_filter:
  enabled: true
  timeframes:
    - H1
    - M15
  method: swing_structure
  neutral_policy: reject
  require_same_direction: true
```

Questions a mesurer:

- Le filtre augmente-t-il expectancy ou seulement winrate ?
- Le nombre de trades reste-t-il suffisant ?
- Est-ce que certains actifs profitent du filtre et d'autres non ?

### 4. Double Consolidation OB/FVG H1 + M15

Hypothese: si une zone H1 et une zone M15 se superposent, la zone est plus pertinente. Un setup M1 pris dans cette zone peut avoir une meilleure qualite.

Regle v1 proposee:

- Detecter les OB/FVG H1.
- Detecter les OB/FVG M15.
- Marquer une zone comme confluente si les prix se chevauchent ou sont proches selon une tolerance.
- Autoriser l'entree M1 seulement si le setup se produit dans ou pres de cette zone.
- Exiger une target claire avec RR minimum.

Parametres possibles:

```yaml
confluence_filter:
  enabled: true
  htf_timeframes:
    - H1
    - M15
  zone_types:
    - OB
    - FVG
  overlap_tolerance_ticks: 4
  require_entry_inside_zone: false
  max_entry_distance_ticks: 8
```

Questions a mesurer:

- La confluence ameliore-t-elle les pertes evitees ?
- Est-ce que les trades deviennent trop rares ?
- La confluence fonctionne-t-elle mieux sur forex, indices ou crypto ?

### 5. Target Claire Et RR Minimum Avant Entree

Hypothese: certains setups sont corrects visuellement mais n'ont pas une target assez claire ou un RR suffisant; les filtrer peut ameliorer l'expectancy.

Regle v1 proposee:

- Calculer le SL structurel.
- Calculer la target CRT et la target liquidite candidate.
- Exiger `RR >= 2.0`.
- Exiger une target directionnelle non ambigue.
- Rejeter le trade si la target est trop proche ou si le SL doit etre trop large.

Parametres possibles:

```yaml
risk_reward_filter:
  enabled: true
  min_rr: 2.0
  reject_if_no_clear_target: true
  target_priority:
    - crt_objective
    - nearest_liquidity
```

## Strategie Candidate: ICT CRT M1 Liquidity Confluence V0

Cette variante combine les idees principales sans aller vers un modele trop complexe.

Definition:

- Timeframe execution: M1.
- Biais: H1 et M15 doivent etre alignes dans le sens du trade.
- Setup: CRT M1 + PD array OB/FVG dans OTE.
- Confluence: bonus ou filtre si OB/FVG H1 et M15 se chevauchent.
- SL: sous/au-dessus de la structure OTE/FVG/OB selectionnee, avec buffer.
- TP initial: objectif CRT.
- TP etendu: liquidite proche si elle reste dans `2.0 * RR_initial`.
- RR minimum: 2.0.
- Pas de trade si target ou SL n'est pas clair.

Configuration indicative:

```yaml
strategy_variant: ICT_CRT_M1_LIQUIDITY_CONFLUENCE_V0
entry_model:
  base: ICT_CRT_M1
  require_m1_crt_setup: true
  require_pd_array: true
  pd_mode: OB_OR_FVG
htf_bias_filter:
  enabled: true
  timeframes: [H1, M15]
  method: swing_structure
  neutral_policy: reject
confluence_filter:
  enabled: false
  htf_timeframes: [H1, M15]
  zone_types: [OB, FVG]
  overlap_tolerance_ticks: 4
stop_model:
  mode: structural_pd_array
  buffer_ticks: 2
  min_rr_after_stop: 2.0
target_model:
  mode: crt_then_nearest_liquidity
  max_rr_multiplier_from_initial: 2.0
  min_initial_rr: 2.0
risk_reward_filter:
  enabled: true
  min_rr: 2.0
```

## Plan De Test

Pour chaque hypothese:

1. Creer un fichier de config dedie: `configs/strategy_<hypothesis>.yaml`.
2. Lancer baseline et variante sur le meme panier d'actifs.
3. Lancer sur au moins deux periodes distinctes si les donnees le permettent.
4. Comparer en `All`, puis par actif.
5. Lire les trades gagnants/perdants en review pour comprendre le mecanisme.
6. Noter le resultat dans un journal de strategie.

Critere d'acceptation possible:

- expectancy amelioree;
- profit factor stable ou meilleur;
- drawdown stable ou meilleur;
- baisse de trade count acceptable;
- pas d'amelioration concentree sur un seul actif/mois;
- pas d'utilisation de donnees futures.

Critere de rejet possible:

- winrate meilleur mais expectancy plus faible;
- variante profitable seulement sur un actif;
- trades trop rares pour conclure;
- drawdown plus violent;
- target extension qui transforme trop de TP initiaux en perdants;
- regle impossible a expliquer simplement.

## Donnees A Ajouter Pour Mieux Diagnostiquer

Features a calculer au moment de l'entree:

- biais H1;
- biais M15;
- distance au previous day high/low;
- distance au previous week/month high/low;
- distance a Asian high/low et London high/low deja formes;
- presence d'un OB/FVG H1 proche;
- presence d'un OB/FVG M15 proche;
- overlap H1/M15;
- distance entree -> SL;
- distance entree -> TP initial;
- distance entree -> TP etendu;
- MFE/MAE pendant le trade;
- raison de sortie;
- gap/data quality autour du trade.

Ces features doivent etre stockees comme metadata de trade ou events de setup pour permettre les dashboards analytiques et, plus tard, un modele ML propre.

## ML Plus Tard

Le ML peut devenir utile quand on aura assez de trades, mais il doit rester un outil de diagnostic avant d'etre un moteur de decision.

Approche saine:

- construire une table de features connues au moment de l'entree;
- predire outcome, RR realise ou probabilite de toucher TP avant SL;
- separer train/test par periode pour eviter le leakage temporel;
- analyser les features importantes;
- convertir les patterns robustes en regles simples testables;
- ne jamais utiliser le modele pour valider une regle sur les memes trades qui l'ont inspiree.

## Format Pour Une Proposition De Strategie

Chaque nouvelle idee doit etre ecrite comme ceci:

```markdown
## Hypothese

Ce que l'on pense ameliorer.

## Regle Deterministe

La regle exacte, sans interpretation visuelle vague.

## Donnees Necessaires

Candles, timeframes, niveaux, events, metadata.

## Parametres

Liste des parametres testables.

## Risques De Biais

Ce qui pourrait utiliser le futur ou sur-optimiser.

## Test

Actifs, dates, baseline, variante, metriques.

## Decision

Accepter, rejeter, garder en observation.
```

## Regle Pour Codex

Quand on demande d'analyser ou d'ameliorer une strategie:

- charger ce playbook;
- lire la config de strategie concernee;
- lire les runs et analytics disponibles;
- distinguer observation, hypothese et regle testable;
- proposer des variantes parametrables;
- indiquer les donnees ou primitives manquantes;
- ne jamais annoncer qu'une variante est meilleure sans backtest comparatif;
- preferer une regle simple et robuste a une regle qui colle trop bien quelques exemples.
