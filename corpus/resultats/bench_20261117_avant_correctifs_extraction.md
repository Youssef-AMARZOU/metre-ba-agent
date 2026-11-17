# Benchmark corpus PlanBA — 2026-11-17T16:38:26

| Plan | Rôle | Statut | Vérifiabilité | Rappel types | Précision | Valeurs justes | **Valeurs fausses** | Manquantes | VT |
|---|---|---|---|---|---|---|---|---|---|
| str_eljadida_type2 | calibrage | mesuré | PARTIEL (81%) | — | — | — | **—** | — | aucune vérité terrain |
| str_eljadida_type4 | evaluation | mesuré | PARTIEL (98%) | — | — | — | **—** | — | aucune vérité terrain |
| extension_archive | evaluation | mesuré | PARTIEL (75%) | 0.588 | 0.909 | 13 | **5** | 4 | brouillon |
| plan_ba_final | evaluation | mesuré | PARTIEL (86%) | — | — | — | **—** | — | aucune vérité terrain |
| plan_ba_r2_ilide | evaluation | mesuré | PARTIEL (60%) | — | — | — | **—** | — | aucune vérité terrain |
| bolbol_goumandey | evaluation | ignoré (lent, --inclure-lents) |  | — | — | — | **—** | — | — |

## extension_archive — détail

- **semelles** : rappel 1.0 · précision 1.0 · VT ['S1', 'S2'] · extrait ['S1', 'S2']
  - S1 → a:FAUX(0.3≠1.1), b:FAUX(0.5≠1.5), h:manquant
  - S2 → a:manquant, b:manquant, h:manquant
- **massifs** : rappel 0.0 · précision None · VT ['MASSIF_60x60x60'] · extrait []
- **longrines** : rappel 0.5 · précision 1.0 · VT ['LG1', 'LG2'] · extrait ['LG2']
  - LG2 → b:FAUX(0.25≠0.3), h:FAUX(0.45≠0.37)
- **poteaux** : rappel 0.2 · précision 1.0 · VT ['P1', 'P2', 'P3', 'P4', 'P5'] · extrait ['P1']
  - P1 → a:ok, b:FAUX(0.5≠0.3)
- **poutres** : rappel 0.857 · précision 0.857 · VT ['N2', 'N3', 'N4', 'N5', 'N6', 'N7', 'PN'] · extrait ['N2', 'N3', 'N4', 'N5', 'N6', 'N7', 'PC']

_Seuls les plans `evaluation` avec vérité terrain `valide` mesurent la généralisation. Une valeur fausse est un défaut bloquant._