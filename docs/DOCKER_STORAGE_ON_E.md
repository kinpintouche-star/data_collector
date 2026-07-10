# Stockage Docker Sur E

Derniere mise a jour: 2026-07-10

## Etat Actuel

- L'application tourne de nouveau correctement.
- La base Postgres contient toujours les donnees attendues:
  - `10,408,591` candles dans `market_candles`;
  - API healthy sur `http://127.0.0.1:8000/api/health`.
- Un dump de securite est stocke sur E:

```text
E:\tradinglab\.docker-data\backups\ict_before_e_bind.dump
```

- Le cache R2 local est dans le projet:

```text
E:\tradinglab\.cache\market_archive
```

## Pourquoi Postgres N'Est Pas Bind-Mounte Directement

On a teste le bind mount direct:

```yaml
./.docker-data/postgres:/var/lib/postgresql/data
```

L'image officielle Postgres echoue au demarrage sur Windows/NTFS:

```text
initdb: error: could not change permissions of directory "/var/lib/postgresql/data": Operation not permitted
```

Meme le contournement `PGDATA=/var/lib/postgresql/data/pgdata` echoue pour la
meme raison. Postgres doit appliquer des permissions Unix sur son repertoire de
donnees; un dossier Windows monte directement ne les supporte pas correctement.

## Solution Propre

La solution propre est de deplacer le stockage Docker Desktop vers E. Comme ca,
le volume nomme Docker `tradinglab_postgres_data` reste un vrai volume Linux,
mais son disque virtuel est physiquement stocke sur E au lieu de C.

Chemin recommande:

```text
E:\tradinglab\docker-desktop-data
```

Procedure manuelle recommandee:

1. Ouvrir Docker Desktop.
2. Aller dans `Settings`.
3. Aller dans `Resources`.
4. Chercher l'option de localisation du disque / disk image.
5. Choisir un dossier sur E, par exemple `E:\tradinglab\docker-desktop-data`.
6. Appliquer et laisser Docker Desktop redemarrer/deplacer ses donnees.
7. Relancer le projet avec:

```powershell
.\scripts\dev.ps1 up
```

## A Ne Pas Faire

- Ne pas supprimer le volume Docker `tradinglab_postgres_data` tant que le dump
  n'a pas ete restaure et valide ailleurs.
- Ne pas utiliser un bind mount NTFS direct pour `/var/lib/postgresql/data`.
- Ne pas lancer `docker-compose down -v`, car `-v` supprime les volumes.

## Verification

Apres deplacement Docker Desktop ou restauration:

```powershell
docker-compose ps
curl.exe -s http://127.0.0.1:8000/api/health
docker-compose exec -T postgres psql -U ict -d ict -tAc "select count(*) from market_candles;"
```
