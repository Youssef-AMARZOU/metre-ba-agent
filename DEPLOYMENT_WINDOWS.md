# Deploiement Windows PlanBA

## Artefact valide

Le build PyInstaller utilise le mode `onedir`. L'artefact complet se trouve
dans `deployment/PlanBA_Metre_Extractor/` et contient
`PlanBA_Metre_Extractor.exe` ainsi que toutes ses DLL et ressources.

Ne copiez pas uniquement le fichier `.exe`. Copiez le dossier entier vers un
emplacement local, par exemple `C:\Outils\PlanBA_Metre_Extractor`.

## Installation sur le PC utilisateur

1. Copiez le dossier `deployment\PlanBA_Metre_Extractor` en conservant toute
   son arborescence.
2. Double-cliquez sur
   `C:\Outils\PlanBA_Metre_Extractor\PlanBA_Metre_Extractor.exe` pour lancer
   l'interface graphique.
3. Pour un test sans interface, ouvrez PowerShell et lancez :

```powershell
& 'C:\Outils\PlanBA_Metre_Extractor\PlanBA_Metre_Extractor.exe' `
  --cli `
  --input 'C:\Plans\PLAN BA final.pdf' `
  --out 'C:\Plans\planba-output'
```

Les chemins entre apostrophes sont indispensables lorsque le nom du plan ou
le dossier contient des espaces. Le dossier de sortie contient
`plan_data.json`, `metre_genere.xlsx`, `optimisation_chantiers.xlsx`,
`rapport_metre.pdf` et `note_calculs_chantier.pdf`.

## Verification

Le CLI et le GUI ont ete demarres depuis le build de cette branche. Le CLI a
traite un PDF de reference avec espaces dans le chemin et a retourne le code
0. Le GUI est reste actif au moins 12 secondes avant son arret de controle.

Le classeur genere utilise cinq feuilles :

- `01_Detail_Quantitatif`
- `02_Armatures`
- `03_Attachement_Ferraillage`
- `04_GO_Attachement`
- `05_Catalogue_Armatures_Standard`

## Limites

Le dossier est autonome mais reste specifique a Windows. Les adaptateurs OCR
lourds et le glisser-deposer peuvent etre absents selon les dependances
installees lors du build. Dans ce cas, le pipeline conserve le texte lisible,
signale la capacite manquante et genere les livrables partiels avec
avertissements.
