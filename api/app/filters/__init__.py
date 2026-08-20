"""The filter DSL and its compiler (M6).

`dsl.py` defines what a filter is; `compiler.py` is the only thing that turns
one into SQL. Keeping them apart is what lets saved filters, ad-hoc search and
— from M8 — assignment-rule conditions share one language instead of growing
three.
"""
