# Structure dataset — plans locaux et datasets externes

Les datasets Kaggle déclarés dans `../config/external_datasets.yaml` ne sont
pas téléchargés ni versionnés dans ce dépôt. Un clone propre doit fonctionner
avec les fixtures de métadonnées uniquement et signaler les datasets absents.

## Téléchargement légal, manuel et reproductible

1. Vérifier sur la page Kaggle la licence et les conditions du contributeur.
2. Télécharger manuellement l'archive depuis Kaggle (ou utiliser le Kaggle
   CLI authentifié après avoir accepté ces conditions).
3. Décompresser chaque archive sous le `local_dir` indiqué dans la
   configuration, avec les sous-dossiers `train/`, `validation/` et `test/`.
4. Conserver localement les notices et annotations d'origine; ne jamais les
   committer. Le validateur ne télécharge rien et ne contacte aucun service.

Les deux entrées sont attribuées à `masterpn` sous **CC BY-NC-SA 4.0** :
attribution obligatoire, usage non commercial uniquement, et toute adaptation
doit être partagée sous la même licence. Vérifier la licence publiée par
Kaggle avant tout usage, notamment redistribution ou usage commercial.

Le protocole aveugle est généré par `core.blind_benchmark` à partir des
inventaires locaux et d'une graine explicite; il sépare chaque source, projet
et split sans mélanger les ensembles.

## Structure dataset interne
#
# Chaque exemple suit le même schéma :
#   dataset/examples/<nom_projet>/
#     input/
#       plan.pdf              # le plan BA (PDF)
#       notes.txt             # (optionnel) notes du métreur, paramètres site
#     output_reference/
#       metre_reference.xlsx  # le métré de référence (format MZINDA ou similaire)
#       rapport_reference.pdf # (optionnel) rapport de métré de référence
#
# Pour traiter un exemple :
#   python run_batch.py --example dataset/examples/001_MZINDA_Youssoufia
#
# Pour traiter tous les exemples :
#   python run_batch.py --all
#
# Le pipeline compare systématiquement la sortie générée avec la référence
# et produit un rapport d'écarts (feuille "Comparaison" + rapport PDF).
