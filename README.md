# Ubiquity

Synchronisation bidirectionnelle de fichiers et de presse-papiers entre plusieurs machines (macOS ↔ Windows ↔ Linux) via TCP, avec une icône dans la barre de menu.

## Fonctionnalités

- **Sync de fichiers** en temps réel, bidirectionnelle, last-write-wins
- **Sync du presse-papiers** (texte) entre les machines connectées
- **Auto-découverte** du serveur par broadcast UDP — pas besoin de connaître l'IP à l'avance
- **Icône tray** avec statut en couleur, transferts en cours, paramètres
- **Filtrage** des fichiers à exclure (`.DS_Store`, `*.tmp`, etc.)
- **Mode headless** pour les machines sans interface graphique

## Architecture

```
Serveur (macOS/Linux)          Client (Windows/macOS/Linux)
  ubiquity.py                    ubiquity.py
       │                               │
  SyncEngine ──── TCP 5000 ────► SyncEngine
  DiscoveryServer ◄─ UDP 5999 ── DiscoveryClient
```

Un seul serveur, un ou plusieurs clients. Le serveur écoute ; les clients se connectent et s'auto-découvrent par broadcast UDP sur le réseau local.

## Prérequis

```
Python 3.11+
pip install -r requirements.txt
```

Dépendances : `watchdog`, `tqdm`, `pystray`, `Pillow`, `pyperclip`

**Linux uniquement** — sync presse-papiers :
```bash
sudo apt install xclip   # ou xsel
```

## Utilisation

### Mode tray (recommandé)

```bash
python ubiquity.py
```

L'application démarre dans la barre de menu et lance la synchronisation automatiquement. Clic sur l'icône pour :
- Voir l'état de la connexion
- Changer de mode (serveur / client)
- Accéder aux paramètres
- Ouvrir le dossier synchronisé
- Consulter les logs

**Signification des couleurs :**

| Couleur | État |
|---------|------|
| Gris | Arrêté |
| Rouge | En attente d'un pair |
| Orange | Transfert en cours |
| Vert | Connecté et synchronisé |

### Mode headless (sans interface)

Pour les serveurs ou machines sans écran :

```bash
# Serveur
python ubiquity_headless.py --mode server --dir /chemin/vers/dossier

# Client (auto-découverte)
python ubiquity_headless.py --mode client --dir /chemin/vers/dossier

# Client (IP fixe)
python ubiquity_headless.py --mode client --dir /chemin/vers/dossier --peer 192.168.1.10
```

## Configuration

La configuration est stockée dans `~/.ubiquity/config.json` et modifiable via l'interface "Paramètres…" du tray ou directement dans le fichier.

```json
{
  "mode": "client",
  "watch_dir": "/Users/moi/Ubiquity",
  "peer": "",
  "port": 5000,
  "exclude": [
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "*.tmp",
    "~$*"
  ]
}
```

| Clé | Description |
|-----|-------------|
| `mode` | `"server"` ou `"client"` |
| `watch_dir` | Dossier à synchroniser |
| `peer` | IP du serveur (vide = auto-découverte) |
| `port` | Port TCP (défaut : `5000`) |
| `exclude` | Patterns fnmatch à ignorer (`*.log`, `build/*`, etc.) |

Les logs sont écrits dans `~/.ubiquity/ubiquity.log` (rotation automatique, 1 Mo max).

## Ports réseau

| Port | Protocole | Rôle |
|------|-----------|------|
| 5000 | TCP | Transfert de fichiers et presse-papiers |
| 5999 | UDP | Auto-découverte du serveur |

## Build — distributable standalone

Les exécutables sont produits avec PyInstaller. Aucune installation de Python requise sur les machines cibles.

### macOS → `.app` + `.dmg`

```bash
pip install pyinstaller
./build.sh
# dist/Ubiquity.app
# dist/Ubiquity.dmg
```

### Windows → installeur sans droits admin

Sur une machine Windows :

```bat
pip install pyinstaller
build.bat             :: → dist\ubiquity.exe
build.bat installer   :: → dist\UbiquitySetup.exe  (nécessite Inno Setup 6)
```

[Inno Setup 6](https://jrsoftware.org/isinfo.php) est gratuit. L'installeur place l'application dans `%LOCALAPPDATA%\Ubiquity` et propose un démarrage automatique avec Windows, sans élévation de privilèges.

### Linux → binaire ELF (via Docker depuis macOS)

```bash
./build.sh --linux
# dist/ubiquity-linux
```

> **Note :** le binaire Linux nécessite GTK et xclip sur la machine cible (`apt install gir1.2-appindicator3-0.1 xclip`).

### CI/CD — les 3 plateformes en parallèle

Un push avec un tag `vX.Y` déclenche le workflow GitHub Actions (`.github/workflows/build.yml`) qui produit les 3 artefacts et crée une GitHub Release automatiquement.

```bash
git tag v1.0 && git push --tags
```
