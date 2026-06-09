"""Planner pipeline: paper -> grounded knowledge graph -> 4-track curriculum.

Stages (DESIGN.md):
  0. ingest      identify the paper (arXiv id / DOI / title) from the document
  1. comprehend  paper-only pass: split presupposed vs introduced concepts,
                 extract claims / results / limits / insights
  2. acquire     retrieve grounded landscape (references) + trends (citing)
  3. assemble    merge into sources[] + graph
  4. project     emit the 4-track curriculum JSON (prereq/landscape/trends/paper)

The comprehend split (presupposed vs introduced) is the axis: presupposed
concepts seed the prereq track's frontier; introduced concepts feed the paper
track (claims/results/limits/insights). See D4.
"""
