#!/usr/bin/env python3
"""Confere estaticamente cada simbolo do Docling usado por pdf_to_md.py
contra o codigo-fonte real do pacote, sem precisar instalar torch."""
import ast, sys
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else 'srcchk/ext')
MODULOS = ['docling/datamodel/pipeline_options.py', 'docling/datamodel/accelerator_options.py',
           'docling/datamodel/base_models.py', 'docling/document_converter.py']
TODAS = {}
for rel in MODULOS:
    tree = ast.parse((SRC / rel).read_text(encoding='utf-8'))
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef):
            TODAS.setdefault(n.name, n)

def campos(nome, herda=True, _vistos=None):
    _vistos = _vistos or set()
    if nome in _vistos or nome not in TODAS: return set()
    _vistos.add(nome)
    cls, out = TODAS[nome], set()
    for n in cls.body:
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name): out.add(n.target.id)
        elif isinstance(n, ast.Assign):
            out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.FunctionDef): out.add(n.name + '()')
    if herda:
        for b in cls.bases:
            bn = b.id if isinstance(b, ast.Name) else getattr(b, 'attr', None)
            if bn: out |= campos(bn, True, _vistos)
    return out

falhas, total = [], 0
def check(cond, desc):
    global total; total += 1
    if not cond: falhas.append(desc)

for c in ["PdfPipelineOptions","RapidOcrOptions","EasyOcrOptions","TesseractOcrOptions",
          "TableFormerMode","TableStructureOptions","AcceleratorOptions","AcceleratorDevice",
          "InputFormat","DocumentConverter","PdfFormatOption","ConversionStatus"]:
    check(c in TODAS, f"classe {c}")

for campo in ["do_ocr","do_table_structure","ocr_options","table_structure_options",
              "accelerator_options","artifacts_path","document_timeout"]:
    check(campo in campos("PdfPipelineOptions"), f"PdfPipelineOptions.{campo}")
for campo in ["mode","do_cell_matching"]:
    check(campo in campos("TableStructureOptions"), f"TableStructureOptions.{campo}")
for eng in ["RapidOcrOptions","EasyOcrOptions","TesseractOcrOptions"]:
    check("lang" in campos(eng), f"{eng}.lang")
for mm in ["FAST","ACCURATE"]:  check(mm in campos("TableFormerMode", False), f"TableFormerMode.{mm}")
for mm in ["AUTO","CPU","CUDA","MPS","XPU"]: check(mm in campos("AcceleratorDevice", False), f"AcceleratorDevice.{mm}")
for mm in ["SUCCESS","PARTIAL_SUCCESS","FAILURE","SKIPPED"]:
    check(mm in campos("ConversionStatus", False), f"ConversionStatus.{mm}")
check("PDF" in campos("InputFormat", False), "InputFormat.PDF")
check("pipeline_options" in campos("PdfFormatOption"), "PdfFormatOption.pipeline_options")
check("convert()" in campos("DocumentConverter"), "DocumentConverter.convert()")
check("backend" in campos("RapidOcrOptions"), "RapidOcrOptions.backend")
check("validate_device()" in campos("AcceleratorOptions"), "AcceleratorOptions.validate_device()")
for campo in ["num_threads","device"]: check(campo in campos("AcceleratorOptions"), f"AcceleratorOptions.{campo}")

print(f"Simbolos conferidos: {total} | divergencias: {len(falhas)}")
for f in falhas: print("  X", f)
sys.exit(1 if falhas else 0)
