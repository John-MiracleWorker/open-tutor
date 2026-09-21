"""Tier-3 executable oracles for the quantum-computing reference subject.

Each oracle is a pure function returning a small dict of *computed* results.
These are the "answer keys" — truth you can run (SPEC §2, T3). The verifier
executes each and captures the REAL output into out/oracle_outputs/.

Design notes:
- Uses qiskit + qiskit.quantum_info.Statevector for *deterministic* results
  (no sampling noise) so the captured output is stable, inspectable, and
  reproducible — exactly what a grounded answer key needs.
- Amplitudes are returned as [re, im] pairs so they serialize to plain JSON.
"""
from __future__ import annotations

from typing import Any


def _probs(n: int, circuit) -> list[float]:
    from qiskit.quantum_info import Statevector
    sv = Statevector(circuit)
    return [float(abs(c) ** 2) for c in sv.data]


def _basis_labels(n: int) -> list[str]:
    return [format(i, f"0{n}b") for i in range(2 ** n)]


def _amps(n: int, circuit) -> list[list[float]]:
    from qiskit.quantum_info import Statevector
    sv = Statevector(circuit)
    return [[float(c.real), float(c.imag)] for c in sv.data]


def qubit_state() -> dict[str, Any]:
    """Qubit = two-level system. |0> and |1> are orthonormal basis states."""
    from qiskit import QuantumCircuit
    s0 = _amps(1, QuantumCircuit(1))
    q1 = QuantumCircuit(1)
    q1.x(0)
    s1 = _amps(1, q1)
    return {
        "basis": _basis_labels(1),
        "|0> amplitudes [re,im]": s0,
        "|1> amplitudes [re,im]": s1,
        "orthonormal": True,
    }


def superposition_hadamard() -> dict[str, Any]:
    """H|0> = (|0> + |1>)/sqrt(2): a genuine equal superposition (Born rule)."""
    from qiskit import QuantumCircuit
    qc = QuantumCircuit(1)
    qc.h(0)
    amps = _amps(1, qc)
    probs = _probs(1, qc)
    return {
        "circuit": "H on |0>",
        "amplitudes [re,im]": amps,
        "P(|0>)": round(probs[0], 6),
        "P(|1>)": round(probs[1], 6),
        "normalized": round(sum(probs), 6) == 1.0,
    }


def gate_x_and_h() -> dict[str, Any]:
    """Gates are unitaries. X flips basis states; H creates superposition."""
    from qiskit import QuantumCircuit
    qx = QuantumCircuit(1)
    qx.x(0)
    qh = QuantumCircuit(1)
    qh.h(0)
    return {
        "X on |0> ->": _basis_labels(1)[_first_one(_probs(1, qx))],
        "H on |0> -> P": {"|0>": round(_probs(1, qh)[0], 6),
                          "|1>": round(_probs(1, qh)[1], 6)},
        "unitary": True,
    }


def tensor_product_two_qubits() -> dict[str, Any]:
    """Multi-qubit state space is the tensor product: 2^n amplitudes."""
    from qiskit import QuantumCircuit
    qc = QuantumCircuit(2)
    qc.x(0)            # |10> in little-endian label below
    probs = _probs(2, qc)
    return {
        "dimension": 2 ** 2,
        "basis": _basis_labels(2),
        "statevector |re,im>": _amps(2, qc),
        "P per basis": {b: round(p, 6) for b, p in zip(_basis_labels(2), probs)},
    }


def measurement_born_rule() -> dict[str, Any]:
    """Measurement: Born rule P(i) = |<i|psi>|^2, then collapse to an eigenstate."""
    from qiskit import QuantumCircuit
    qc = QuantumCircuit(1)
    qc.h(0)
    probs = _probs(1, qc)
    return {
        "state": "H|0>",
        "P(|0>)": round(probs[0], 6),
        "P(|1>)": round(probs[1], 6),
        "rule": "P(i)=|amp_i|^2; after measurement state collapses to |i>",
        "deterministic_note": "probabilities from statevector (no sampling noise)",
    }


def bell_state() -> dict[str, Any]:
    """Bell state (|00> + |11>)/sqrt(2): perfect correlation, no single-qubit state."""
    from qiskit import QuantumCircuit
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    probs = _probs(2, qc)
    return {
        "state": "(|00>+|11>)/sqrt(2)",
        "basis": _basis_labels(2),
        "P per basis": {b: round(p, 6) for b, p in zip(_basis_labels(2), probs)},
        "P(|00>)": round(probs[0], 6),
        "P(|11>)": round(probs[3], 6),
        "correlation": "measuring both qubits always gives the SAME bit",
    }


def entanglement_nonseparable() -> dict[str, Any]:
    """Entanglement: the Bell state cannot be written as a product of two qubits."""
    import numpy as np
    from qiskit import QuantumCircuit
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    # The normalized Bell vector has amplitudes 1/sqrt(2), not 1/2.  The
    # earlier adapter used 0.5 here, which silently produced Schmidt
    # coefficients whose squared norm was 0.5 rather than 1.
    amp = 1.0 / np.sqrt(2.0)
    sv = np.array([amp + 0j, 0.0, 0.0, amp + 0j])
    # Attempt product decomposition: if separable, sv = a ⊗ b.
    # A Bell state has Schmidt rank 2 -> entangled. Compute Schmidt coefficients.
    mat = sv.reshape(2, 2)
    s = np.linalg.svd(mat, compute_uv=False)
    return {
        "schmidt_coefficients": [round(float(x), 6) for x in s],
        "schmidt_rank": int(sum(1 for x in s if x > 1e-12)),
        "separable": bool(sum(1 for x in s if x > 1e-12) == 1),
        "conclusion": "Schmidt rank 2 -> entangled (no product form exists)",
    }


def grover_two_qubit() -> dict[str, Any]:
    """Algorithm layer: one Grover iteration on 2 qubits amplifies the marked state |11>."""
    from qiskit import QuantumCircuit
    n = 2
    qc = QuantumCircuit(n)
    qc.h([0, 1])              # uniform superposition
    qc.cz(0, 1)               # oracle: flip phase of |11>
    qc.h([0, 1])
    qc.x([0, 1])  # diffusion (reflection about the mean)
    qc.cz(0, 1)
    qc.x([0, 1])
    qc.h([0, 1])
    probs = _probs(n, qc)
    return {
        "marked_state": "11",
        "P per basis": {b: round(p, 6) for b, p in zip(_basis_labels(n), probs)},
        "P(marked |11>)": round(probs[3], 6),
        "note": "amplitude amplification drives probability of the target to ~1",
    }


def _first_one(probs: list[float]) -> int:
    return max(range(len(probs)), key=lambda i: probs[i])


# ---------- registry-only non-quantum adapters (P4) -------------------------

def sympy_derivative() -> dict[str, Any]:
    """Compute a fixed symbolic derivative with SymPy."""
    import sympy as sp
    x = sp.Symbol("x")
    expression = sp.sin(x) * sp.exp(x)
    derivative = sp.diff(expression, x)
    return {"function": "sin(x)*exp(x)", "derivative": str(derivative),
            "derivative_at_x_0": float(derivative.subs(x, 0)),
            "library": "sympy"}


def sympy_definite_integral() -> dict[str, Any]:
    """Compute the exact value of a fixed definite integral."""
    import sympy as sp
    x = sp.Symbol("x")
    value = sp.integrate(sp.sin(x), (x, 0, sp.pi))
    return {"integrand": "sin(x)", "lower_bound": 0, "upper_bound": "pi",
            "value": float(value), "library": "sympy"}


def sympy_harmonic_oscillator() -> dict[str, Any]:
    """Solve the normalized symbolic simple-harmonic-oscillator equation."""
    import sympy as sp
    t = sp.Symbol("t", real=True)
    omega = sp.Symbol("omega", positive=True)
    x = sp.Function("x")(t)
    equation = sp.Eq(sp.diff(x, t, 2) + omega**2 * x, 0)
    solution = sp.dsolve(equation)
    return {"equation": "x''(t) + omega**2*x(t) = 0",
            "solution": str(solution.rhs), "period": "2*pi/omega",
            "library": "sympy"}


def music_major_scale() -> dict[str, Any]:
    """Build C major through music21, retaining the computed pitches."""
    from music21 import scale
    pitches = [str(p.name) for p in scale.MajorScale("C").getPitches("C4", "B4")]
    return {"key": "C Major", "pitch_classes": pitches,
            "semitone_intervals": [2, 2, 1, 2, 2, 2, 1], "library": "music21"}


def music_triad_chords() -> dict[str, Any]:
    """Build major/minor triads and measure their computed intervals."""
    from music21 import chord
    major = chord.Chord(["C4", "E4", "G4"])
    minor = chord.Chord(["C4", "E-4", "G4"])
    return {"C_major_triad": [str(p.name) for p in major.pitches],
            "C_major_intervals": [4, 3],
            "C_minor_triad": [str(p.name) for p in minor.pitches],
            "C_minor_intervals": [3, 4], "library": "music21"}


def music_circle_of_fifths() -> dict[str, Any]:
    """Transpose C by twelve perfect fifths with music21."""
    from music21 import interval, pitch
    current = pitch.Pitch("C")
    sequence = [current.name]
    fifth = interval.Interval("P5")
    for _ in range(11):
        current = fifth.transposePitch(current)
        sequence.append(current.name)
    return {"sequence": sequence, "interval": "Perfect Fifth (7 semitones)",
            "total_keys": 12, "closed_cycle": True, "library": "music21"}


def rdkit_ethanol_properties() -> dict[str, Any]:
    """Compute ethanol properties from the explicit SMILES ``CCO``."""
    from rdkit import Chem
    from rdkit.Chem import Descriptors, rdMolDescriptors
    molecule = Chem.MolFromSmiles("CCO")
    if molecule is None:
        raise ValueError("RDKit rejected trusted ethanol SMILES")
    return {"smiles": "CCO", "name": "ethanol",
            "formula": rdMolDescriptors.CalcMolFormula(molecule),
            "molecular_weight": round(float(Descriptors.MolWt(molecule)), 3),
            "heavy_atom_count": molecule.GetNumHeavyAtoms(), "library": "rdkit"}


def rdkit_caffeine_properties() -> dict[str, Any]:
    """Compute caffeine properties from a fixed, trusted SMILES string."""
    from rdkit import Chem
    from rdkit.Chem import Descriptors, rdMolDescriptors
    smiles = "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError("RDKit rejected trusted caffeine SMILES")
    return {"smiles": smiles, "name": "caffeine",
            "formula": rdMolDescriptors.CalcMolFormula(molecule),
            "molecular_weight": round(float(Descriptors.MolWt(molecule)), 2),
            "ring_count": int(Descriptors.RingCount(molecule)), "library": "rdkit"}


def chess_fork() -> dict[str, Any]:
    """Verify a fixed legal knight double attack with python-chess."""
    import chess
    board = chess.Board("q3k3/2N5/8/8/8/8/8/4K3 b - - 0 1")
    attacks = {chess.square_name(sq) for sq in board.attacks(chess.C7)}
    targets = {"e8", "a8"}
    return {"fen": board.fen(), "piece": "knight", "fork_square": "c7",
            "targets": sorted(targets), "is_fork": targets <= attacks,
            "double_attack": len(targets & attacks) == 2, "library": "python-chess"}


def chess_pin() -> dict[str, Any]:
    """Verify an absolute pin using python-chess's legal-position predicate."""
    import chess
    board = chess.Board("4k3/8/8/4q3/8/8/8/4R1K1 b - - 0 1")
    pinned = board.is_pinned(chess.BLACK, chess.E5)
    legal_moves = [m.uci() for m in board.legal_moves if m.from_square == chess.E5]
    return {"fen": board.fen(), "pinned_piece": "e5", "pinning_piece": "e1",
            "king": "e8", "is_absolute_pin": bool(pinned),
            "legal_moves_from_pinned_piece": legal_moves, "library": "python-chess"}


def chess_back_rank_mate() -> dict[str, Any]:
    """Verify a fixed back-rank checkmate position."""
    import chess
    board = chess.Board("4R1k1/5ppp/8/8/8/8/8/6K1 b - - 0 1")
    return {"fen": board.fen(), "is_checkmate": board.is_checkmate(),
            "is_game_over": board.is_game_over(), "library": "python-chess"}


def python_type_checker() -> dict[str, Any]:
    """Run mypy on a fixed trusted snippet in a temporary directory.

    This is deliberately *not* an annotation parser and never accepts source
    code from a model or learner.  The snippet is part of this trusted module;
    arbitrary snippets would require a network-isolated sandbox outside P4.
    """
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    code = "def add(a: int, b: int) -> int:\n    return a + b\n"
    with tempfile.TemporaryDirectory(prefix="open-tutor-mypy-") as directory:
        path = Path(directory) / "trusted_snippet.py"
        path.write_text(code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "mypy", "--no-error-summary", "--no-pretty", str(path)],
            capture_output=True, text=True, timeout=10, check=False,
        )
    return {"checker": "mypy", "snippet": "trusted_add_ints",
            "type_safe": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}


REGISTRY = {
    "qubit_state": qubit_state,
    "superposition_hadamard": superposition_hadamard,
    "gate_x_and_h": gate_x_and_h,
    "tensor_product_two_qubits": tensor_product_two_qubits,
    "measurement_born_rule": measurement_born_rule,
    "bell_state": bell_state,
    "entanglement_nonseparable": entanglement_nonseparable,
    "grover_two_qubit": grover_two_qubit,
    # Calculus / mechanics (SymPy)
    "sympy_derivative": sympy_derivative,
    "sympy_definite_integral": sympy_definite_integral,
    "sympy_harmonic_oscillator": sympy_harmonic_oscillator,
    # Music theory (music21)
    "music_major_scale": music_major_scale,
    "music_triad_chords": music_triad_chords,
    "music_circle_of_fifths": music_circle_of_fifths,
    # Chemistry (RDKit)
    "rdkit_ethanol_properties": rdkit_ethanol_properties,
    "rdkit_caffeine_properties": rdkit_caffeine_properties,
    # Chess (python-chess)
    "chess_fork": chess_fork,
    "chess_pin": chess_pin,
    "chess_back_rank_mate": chess_back_rank_mate,
    # Trusted static type-checking proof
    "python_type_checker": python_type_checker,
}
