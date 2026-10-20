#!/usr/bin/env python3
"""Convierte el STEP del R-2000iC/165F en mallas ligeras por eslabón para el visor sim3d.

Requiere OpenCascade (no está en requirements.txt para no cargar el proyecto):
  python3 -m venv ~/.cache/sim3d-cad-venv
  ~/.cache/sim3d-cad-venv/bin/pip install cadquery-ocp numpy fast-simplification
  ~/.cache/sim3d-cad-venv/bin/python scripts/step_to_robot_mesh.py <modelo.stp> out/sim3d/robot_r2000ic165f.json

Cada malla se guarda en el marco de su articulación (origen en el punto del eje de
JOINT_ORIGINS, ejes alineados con la pose cero del STEP), así el visor solo tiene
que encadenar las rotaciones de palca.robot.r2000.link_frames. El STEP y el JSON
generado son del usuario (modelo de terceros): no se suben al repositorio.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDF import TDF_ChildIterator, TDF_Label
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS
from OCP.XCAFDoc import XCAFDoc_DocumentTool

# Pieza del STEP -> eslabón; mismo origen que palca.robot.r2000.JOINT_ORIGINS.
PART_LINK = {
    "3D_J1": "base", "3D_B205": "base", "3D_J2Base": "j1", "3D_BalancerCase": "balancer_case",
    "3D_BalancerRod": "balancer_rod", "3D_J2Arm": "j2", "3D_J3": "j3", "3D_J4": "j4", "3D_J5": "j5", "3D_J6": "j6",
}
JOINT_ORIGINS = {
    "base": (0, 0, 0), "j1": (0, 0, 0), "j2": (312, 0, 670), "j3": (312, 0, 1745),
    "j4": (312, 0, 1970), "j5": (1592, 0, 1970), "j6": (1592, 0, 1970),
    # Compensador: la camisa pivota en el carro (j1) y el vástago en el brazo (j2).
    "balancer_case": (-470, 189, 621), "balancer_rod": (98, 189, 652),
}
COLORS = {"base": "#2b2b2b", "j1": "#f2c500", "j2": "#f2c500", "j3": "#f2c500", "j4": "#f2c500",
          "j5": "#f2c500", "j6": "#3a3a3a", "balancer_case": "#3a3a3a", "balancer_rod": "#b0b0b0"}


def children(label):
    it = TDF_ChildIterator(label, False)
    while it.More():
        yield it.Value()
        it.Next()


def name(label) -> str:
    attr = TDataStd_Name()
    return attr.Get().ToExtString() if label.FindAttribute(TDataStd_Name.GetID_s(), attr) else "?"


def triangulate(shape, lin: float, ang: float):
    BRepMesh_IncrementalMesh(shape, lin, False, ang, True)
    verts, tris, offset = [], [], 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i).Transformed(trsf)
                verts.append((p.X(), p.Y(), p.Z()))
            rev = face.Orientation() == TopAbs_REVERSED
            for i in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(i).Get()
                tris.append((offset + a - 1, offset + c - 1, offset + b - 1) if rev else (offset + a - 1, offset + b - 1, offset + c - 1))
            offset += tri.NbNodes()
        exp.Next()
    return np.array(verts, dtype=np.float64), np.array(tris, dtype=np.int64)


def simplify(v, f, target):
    if len(f) <= target:
        return v, f
    try:
        import fast_simplification
    except ImportError:
        return v, f
    v2, f2 = fast_simplification.simplify(v.astype(np.float32), f.astype(np.int32), target_reduction=1 - target / len(f))
    return v2.astype(np.float64), f2.astype(np.int64)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--deflection", type=float, default=2.0, help="Tolerancia lineal de teselado (mm)")
    ap.add_argument("--max-tris", type=int, default=12000, help="Triángulos máximos por eslabón tras simplificar")
    args = ap.parse_args(argv)

    doc = TDocStd_Document(TCollection_ExtendedString("doc"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadFile(str(args.step)) != 1:
        sys.exit(f"No se pudo leer {args.step}")
    reader.Transfer(doc)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    meshes: dict[str, list] = {}
    for lab in children(st.Label()):
        if not st.IsAssembly_s(lab):
            continue
        for comp in children(lab):
            if not st.IsComponent_s(comp):
                continue
            ref = TDF_Label()
            st.GetReferredShape_s(comp, ref)
            part = name(ref)
            link = PART_LINK.get(part)
            if link is None:
                print(f"[mesh] pieza sin eslabón, se omite: {part}")
                continue
            v, f = triangulate(st.GetShape_s(comp), args.deflection, 0.35)
            meshes.setdefault(link, []).append((v, f))
            print(f"[mesh] {part} -> {link}: {len(f)} triángulos")
    links = {}
    for link, parts in meshes.items():
        vs, fs, off = [], [], 0
        for v, f in parts:
            vs.append(v); fs.append(f + off); off += len(v)
        v, f = simplify(np.vstack(vs), np.vstack(fs), args.max_tris)
        v = v - np.array(JOINT_ORIGINS[link], dtype=np.float64)
        lo, hi = v.min(axis=0), v.max(axis=0)
        links[link] = dict(
            color=COLORS[link], origin=list(JOINT_ORIGINS[link]),
            aabb=[lo.round(1).tolist(), hi.round(1).tolist()], triangles=int(len(f)),
            positions=base64.b64encode(v.astype("<f4").tobytes()).decode(),
            indices=base64.b64encode(f.astype("<u4").tobytes()).decode())
        print(f"[mesh] {link}: {len(f)} triángulos tras simplificar, aabb local {lo.round(0)} {hi.round(0)}")
    out = dict(schema="palca-robot-mesh/1", model="FANUC R-2000iC/165F", source=args.step.name, units="mm",
               frames="origen en el eje de cada articulación, ejes de la pose cero del STEP", links=links)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out), encoding="utf-8")
    print(f"[mesh] {args.out}: {args.out.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
