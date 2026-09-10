"""
Pipeline modulaire d'extraction de metre BA.
Principe directeur: rien n'est suppose, tout est appris du plan courant.

Architecture:
  FormatDetector -> ZoneSegmenter -> LegendLearner -> TableLearner
       -> GridLearner -> ElementScanner -> EntityResolver
       -> QuantityCalculator -> Reconciler -> ValidationGate -> Metre
"""
