"""Curriculum DESIGNER — the generator half of the pipeline (SPEC §4, stages 1–5).

For the reference subject (quantum computing) this is a *curated* generator: the
concept DAG, per-node misconceptions, per-node coverage keywords, and the
corpus/oracle wiring. In a full build these stages are LLM-driven (SCOPE, DRAFT
DAG, SOURCE FIND, ORACLE FIND, MISCONCEPTIONS) with retrieval over Wikipedia/
Wikidata/open syllabi — but the *shape* of the artifact is identical, and the
PoC's job is to prove the verifier can judge any such artifact honestly.

The generator deliberately does NOT self-declare groundedness — that is the
verifier's job (SPEC §4.2). The generator emits candidates; the verifier decides.
"""
from __future__ import annotations

from .spec import CorpusSource, CurriculumSpec, Misconception, Node

# ---- anchor corpus (authoritative, live) -----------------------------------
_ANCHORS = [
    CorpusSource(
        id="preskill-ph229",
        name="S. Preskill, 'Quantum Computation and Quantum Information' (Phys 229) — Caltech",
        url="https://preskill.caltech.edu/ph229/",
        tier=2,
    ),
    CorpusSource(
        id="ibm-quantum-learning",
        name="IBM Quantum Learning (successor to the Qiskit Textbook)",
        url="https://learning.quantum.ibm.com/",
        tier=2,
    ),
    CorpusSource(
        id="nielsen-chuang",
        # Keep the stable curriculum ID, but use the actual arXiv record. This
        # identifier is Deutsch, Barenco & Ekert's *Universality in Quantum
        # Computation*, not Nielsen & Chuang's textbook.
        name="D. Deutsch, A. Barenco & A. Ekert, 'Universality in Quantum Computation' (arXiv:quant-ph/9505018)",
        url="https://arxiv.org/abs/quant-ph/9505018",
        tier=2,
    ),
]

# ---- per-concept sources (authoritative, live, server-rendered) ------------
_CONCEPT_SOURCES = {
    "qubit": CorpusSource("wiki-qubit", "Wikipedia: Qubit (concept reference)",
                          "https://en.wikipedia.org/wiki/Qubit", tier=2),
    "superposition": CorpusSource("wiki-superposition", "Wikipedia: Quantum superposition",
                                  "https://en.wikipedia.org/wiki/Quantum_superposition", tier=2),
    "gate": CorpusSource("wiki-quantum-gate", "Wikipedia: Quantum gate",
                         "https://en.wikipedia.org/wiki/Quantum_gate", tier=2),
    "tensor_product": CorpusSource("wiki-quantum-computer", "Wikipedia: Quantum computer (multi-qubit state space)",
                                  "https://en.wikipedia.org/wiki/Quantum_computer", tier=2),
    "measurement": CorpusSource("wiki-measurement", "Wikipedia: Measurement in quantum mechanics",
                               "https://en.wikipedia.org/wiki/Measurement_(quantum_mechanics)", tier=2),
    "bell_state": CorpusSource("wiki-bell-state", "Wikipedia: Bell state",
                              "https://en.wikipedia.org/wiki/Bell_state", tier=2),
    "entanglement": CorpusSource("wiki-entanglement", "Wikipedia: Quantum entanglement",
                                "https://en.wikipedia.org/wiki/Quantum_entanglement", tier=2),
    "grover": CorpusSource("wiki-grover", "Wikipedia: Grover's algorithm",
                          "https://en.wikipedia.org/wiki/Grover%27s_algorithm", tier=2),
}


def generate_quantum_computing() -> CurriculumSpec:
    nodes = [
        Node(
            id="qubit",
            title="The Qubit",
            defn="A two-level quantum system. The state is a unit vector in a 2-D "
                 "complex Hilbert space, with |0> and |1> as the computational basis.",
            misconceptions=[
                Misconception(id="qubit-m1", text="A qubit is just a bit that is either 0 or 1 that we don't know which."),
            ],
            grounding_corpus=["wiki-qubit", "nielsen-chuang", "ibm-quantum-learning"],
            oracle="qubit_state",
            covers_keywords=["qubit"],
        ),
        Node(
            id="superposition",
            title="Superposition",
            defn="A qubit can be in a linear combination a|0> + b|1> with complex "
                 "amplitudes; the Hadamard gate creates the equal superposition "
                 "(|0>+|1>)/sqrt(2).",
            prereqs=["qubit"],
            misconceptions=[
                Misconception(id="super-m1", text="Superposition means the qubit is secretly 0 or 1 and we just haven't looked."),
            ],
            grounding_corpus=["wiki-superposition", "ibm-quantum-learning", "preskill-ph229"],
            oracle="superposition_hadamard",
            covers_keywords=["superposition"],
        ),
        Node(
            id="gate",
            title="Quantum Gates (Unitary Operations)",
            defn="Gates are unitary operators acting on the qubit. X flips basis "
                 "states; H creates superposition. Unitaries preserve norm and are reversible.",
            prereqs=["superposition"],
            misconceptions=[
                Misconception(id="gate-m1", text="A quantum gate is a classical logic gate that happens to be fast."),
            ],
            grounding_corpus=["wiki-quantum-gate", "ibm-quantum-learning", "preskill-ph229"],
            oracle="gate_x_and_h",
            covers_keywords=["gate"],
        ),
        Node(
            id="tensor_product",
            title="Multi-Qubit States (Tensor Product)",
            defn="The state space of n qubits is the tensor product of n two-D spaces, "
                 "giving 2^n amplitudes. A product state is |psi> ⊗ |phi>.",
            prereqs=["qubit", "superposition"],
            misconceptions=[
                Misconception(id="tensor-m1", text="Two qubits just carry two independent bits of information."),
            ],
            grounding_corpus=["wiki-quantum-computer", "nielsen-chuang", "preskill-ph229"],
            oracle="tensor_product_two_qubits",
            covers_keywords=["tensor", "product"],
        ),
        Node(
            id="measurement",
            title="Measurement & the Born Rule",
            defn="Measuring in the computational basis yields i with probability "
                 "|<i|psi>|^2, after which the state collapses to |i>.",
            prereqs=["superposition"],
            misconceptions=[
                Misconception(id="meas-m1", text="Measurement just reads out the value the qubit already had."),
            ],
            grounding_corpus=["wiki-measurement", "ibm-quantum-learning", "preskill-ph229"],
            oracle="measurement_born_rule",
            covers_keywords=["measurement", "born"],
        ),
        Node(
            id="bell_state",
            title="Bell States",
            defn="Maximally entangled two-qubit states, e.g. (|00>+|11>)/sqrt(2). "
                 "Measuring one qubit instantly fixes the other's outcome.",
            prereqs=["tensor_product", "measurement"],
            misconceptions=[
                Misconception(id="bell-m1", text="A Bell state is two qubits that are both definitely 0 or both definitely 1."),
            ],
            grounding_corpus=["wiki-bell-state", "ibm-quantum-learning", "preskill-ph229"],
            oracle="bell_state",
            covers_keywords=["bell", "entangled"],
        ),
        Node(
            id="entanglement",
            title="Entanglement",
            defn="A state is entangled when it cannot be written as a product of "
                 "states of its subsystems. The Bell state has Schmidt rank 2.",
            prereqs=["tensor_product"],
            misconceptions=[
                Misconception(id="ent-m1", text="Entanglement is faster-than-light communication."),
                Misconception(id="ent-m2", text="Entangled particles are just correlated like two gloves in separate boxes."),
            ],
            grounding_corpus=["wiki-entanglement", "preskill-ph229", "nielsen-chuang"],
            oracle="entanglement_nonseparable",
            covers_keywords=["entanglement", "entangled"],
        ),
        Node(
            id="grover",
            title="Grover's Search Algorithm",
            defn="Amplitude amplification: a marked state's amplitude is boosted by "
                 "repeating an oracle + diffusion (reflection about the mean) step. "
                 "Finds a marked item in an unsorted set of N in ~sqrt(N) queries.",
            prereqs=["gate", "measurement"],
            misconceptions=[
                Misconception(id="grover-m1", text="Grover's algorithm searches by checking every item in parallel at once."),
            ],
            grounding_corpus=["wiki-grover", "ibm-quantum-learning", "preskill-ph229"],
            oracle="grover_two_qubit",
            covers_keywords=["grover", "amplification"],
        ),
    ]

    # de-duplicate corpus (anchors first, then concept sources, by id)
    seen = set()
    corpus = []
    for c in _ANCHORS + [v for v in _CONCEPT_SOURCES.values()]:
        if c.id not in seen:
            seen.add(c.id)
            corpus.append(c)

    return CurriculumSpec(
        subject="quantum-computing",
        title="Quantum Computing — foundations to a first algorithm",
        scope={
            "depth": "foundations + one algorithm (Grover)",
            "level": "curious non-physicist, math-comfortable",
            "assumed_prereqs": ["linear algebra (vectors, complex numbers)",
                               "probability basics"],
            "goal": "a working mental model of qubits, gates, measurement, entanglement, "
                    "and amplitude amplification, with every claim grounded or runnable",
        },
        tiers={"canonical": "t1", "corpus": "t2", "oracle": "t3 (qiskit/qiskit-aer)",
               "assessment": "t4 (generated rubrics + teach-back)"},
        corpus=corpus,
        oracle={"id": "qiskit", "sandbox": "python + qiskit + qiskit-aer",
                "notes": "statevector-based for deterministic, reproducible answer keys"},
        nodes=nodes,
        generator_note="Curated reference curriculum (SPEC §4). In P4 these stages are "
                       "LLM-driven with retrieval; artifact shape is identical.",
    )


# ---------- honest curated P4 starters -------------------------------------

def _starter_source(subject: str, key: str, name: str, url: str) -> CorpusSource:
    """Namespace curated IDs so one subject cannot read another's cache."""
    return CorpusSource(f"{subject}-{key}", name, url, tier=2)


def _starter_spec(subject: str, title: str, level: str, depth: str,
                  prereqs: list[str], goal: str, corpus: list[CorpusSource],
                  nodes: list[Node], oracle_id: str, notes: str) -> CurriculumSpec:
    valid = {c.id for c in corpus}
    if any(set(n.grounding_corpus) - valid for n in nodes):
        raise ValueError(f"{subject}: a node references a source outside its corpus")
    return CurriculumSpec(
        subject=subject, title=title,
        scope={"depth": depth, "level": level, "assumed_prereqs": prereqs, "goal": goal},
        tiers={"canonical": "t1", "corpus": "t2", "oracle": "t3 (" + oracle_id + ")",
               "assessment": "t4"},
        corpus=corpus, oracle={"id": oracle_id, "sandbox": notes}, nodes=nodes,
        generator_note="Curated P4 starter. Groundedness and source metadata are set by the verifier.",
    )


def generate_calculus() -> CurriculumSpec:
    subject = "calculus"
    corpus = [
        _starter_source(subject, "wiki-calculus", "Wikipedia: Calculus", "https://en.wikipedia.org/wiki/Calculus"),
        _starter_source(subject, "wiki-derivative", "Wikipedia: Derivative", "https://en.wikipedia.org/wiki/Derivative"),
        _starter_source(subject, "wiki-integral", "Wikipedia: Integral", "https://en.wikipedia.org/wiki/Integral"),
        _starter_source(subject, "wiki-harmonic-oscillator", "Wikipedia: Harmonic oscillator", "https://en.wikipedia.org/wiki/Harmonic_oscillator"),
    ]
    c = {x.id: x for x in corpus}
    nodes = [
        Node("derivative", "The Derivative",
             "The instantaneous rate of change of a function, defined as the limit of its difference quotient.",
             misconceptions=[Misconception("calculus-derivative-m1", "A derivative is the fraction 0/0 rather than a limit." )],
             grounding_corpus=[c[subject + "-wiki-derivative"].id, c[subject + "-wiki-calculus"].id],
             oracle="sympy_derivative", covers_keywords=["derivative", "rate of change"]),
        Node("integral", "The Integral",
             "A definite integral is the limit of accumulating Riemann sums; differentiation and integration are linked by the Fundamental Theorem.",
             prereqs=["derivative"],
             misconceptions=[Misconception("calculus-integral-m1", "An integral is only an antiderivative formula, not an accumulation limit.")],
             grounding_corpus=[c[subject + "-wiki-integral"].id, c[subject + "-wiki-calculus"].id],
             oracle="sympy_definite_integral", covers_keywords=["integral", "riemann"]),
        Node("harmonic-oscillator", "Simple Harmonic Oscillator",
             "A system with restoring force proportional to displacement, whose motion obeys x'' + omega^2 x = 0.",
             prereqs=["derivative", "integral"],
             misconceptions=[Misconception("calculus-harmonic-m1", "The restoring force grows with the square of displacement.")],
             grounding_corpus=[c[subject + "-wiki-harmonic-oscillator"].id, c[subject + "-wiki-calculus"].id],
             oracle="sympy_harmonic_oscillator", covers_keywords=["harmonic oscillator", "restoring force"]),
    ]
    return _starter_spec(subject, "Calculus — change, accumulation, and motion",
                         "introductory college mathematics", "derivatives, integrals, and one differential-equation application",
                         ["algebra", "trigonometry"], "Connect rates of change and accumulation to a runnable mechanics example.",
                         corpus, nodes, "SymPy", "Python + SymPy; fixed symbolic expressions only")


def generate_music_theory() -> CurriculumSpec:
    subject = "music-theory"
    corpus = [
        _starter_source(subject, "wiki-music-theory", "Wikipedia: Music theory", "https://en.wikipedia.org/wiki/Music_theory"),
        _starter_source(subject, "wiki-major-scale", "Wikipedia: Major scale", "https://en.wikipedia.org/wiki/Major_scale"),
        _starter_source(subject, "wiki-triad", "Wikipedia: Triad (music)", "https://en.wikipedia.org/wiki/Triad_(music)"),
        _starter_source(subject, "wiki-circle-of-fifths", "Wikipedia: Circle of fifths", "https://en.wikipedia.org/wiki/Circle_of_fifths"),
    ]
    nodes = [
        Node("major-scale", "The Major Scale",
             "A seven-note diatonic scale whose octave step pattern is whole, whole, half, whole, whole, whole, half.",
             misconceptions=[Misconception("music-scale-m1", "Every major scale must start on the pitch C.")],
             grounding_corpus=[f"{subject}-wiki-major-scale", f"{subject}-wiki-music-theory"],
             oracle="music_major_scale", covers_keywords=["major scale", "diatonic"]),
        Node("triad", "Triad Harmony",
             "A triad contains a root, third, and fifth; major and minor triads reverse the sizes of their third intervals.",
             prereqs=["major-scale"],
             misconceptions=[Misconception("music-triad-m1", "A triad is any arbitrary collection of three simultaneous notes.")],
             grounding_corpus=[f"{subject}-wiki-triad", f"{subject}-wiki-music-theory"],
             oracle="music_triad_chords", covers_keywords=["triad", "chord"]),
        Node("circle-of-fifths", "Circle of Fifths",
             "A cyclic arrangement of twelve chromatic pitch classes in which adjacent keys are a perfect fifth apart.",
             prereqs=["triad"],
             misconceptions=[Misconception("music-circle-m1", "The circle of fifths applies only to sharp keys, not flats.")],
             grounding_corpus=[f"{subject}-wiki-circle-of-fifths", f"{subject}-wiki-music-theory"],
             oracle="music_circle_of_fifths", covers_keywords=["circle of fifths", "perfect fifth"]),
    ]
    return _starter_spec(subject, "Music Theory — scales, triads, and key relationships",
                         "foundational Western music theory", "diatonic scales, triad construction, and key relationships",
                         ["basic pitch literacy"], "Build a concrete harmonic vocabulary with computed pitch and interval examples.",
                         corpus, nodes, "music21", "Python + music21; fixed notation examples only")


def generate_chemistry() -> CurriculumSpec:
    subject = "chemistry"
    corpus = [
        _starter_source(subject, "wiki-atom", "Wikipedia: Atom", "https://en.wikipedia.org/wiki/Atom"),
        _starter_source(subject, "wiki-chemical-bond", "Wikipedia: Chemical bond", "https://en.wikipedia.org/wiki/Chemical_bond"),
        _starter_source(subject, "wiki-ethanol", "Wikipedia: Ethanol", "https://en.wikipedia.org/wiki/Ethanol"),
        _starter_source(subject, "wiki-caffeine", "Wikipedia: Caffeine", "https://en.wikipedia.org/wiki/Caffeine"),
    ]
    nodes = [
        Node("atom", "Atoms and Elements",
             "An atom is the smallest unit retaining an element's chemical identity, with a nucleus and surrounding electrons.",
             misconceptions=[Misconception("chem-atom-m1", "An atom is indivisible and has no internal structure.")],
             grounding_corpus=[f"{subject}-wiki-atom"], oracle="rdkit_ethanol_properties",
             covers_keywords=["atom", "element"]),
        Node("chemical-bond", "Chemical Bonding",
             "A chemical bond is an interaction that holds atoms together in a molecule or extended structure.",
             prereqs=["atom"],
             misconceptions=[Misconception("chem-bond-m1", "All chemical bonds are identical electron-sharing links.")],
             grounding_corpus=[f"{subject}-wiki-chemical-bond", f"{subject}-wiki-ethanol"],
             oracle="rdkit_ethanol_properties", covers_keywords=["chemical bond", "bond"]),
        Node("molecular-properties", "Molecular Formula and Properties",
             "A molecular graph encodes connected atoms and bonds, from which formula and selected physical descriptors can be computed.",
             prereqs=["chemical-bond"],
             misconceptions=[Misconception("chem-properties-m1", "A molecular formula alone specifies every structural arrangement.")],
             grounding_corpus=[f"{subject}-wiki-ethanol", f"{subject}-wiki-caffeine"],
             oracle="rdkit_caffeine_properties", covers_keywords=["molecular", "formula"]),
    ]
    return _starter_spec(subject, "Chemistry — atoms, bonds, and molecular structure",
                         "introductory general chemistry", "atoms through molecular descriptors",
                         ["basic algebra"], "Relate chemical vocabulary to explicit molecular graphs and computed descriptors.",
                         corpus, nodes, "RDKit", "Python + RDKit; fixed trusted SMILES examples only")


def generate_chess() -> CurriculumSpec:
    subject = "chess"
    corpus = [
        _starter_source(subject, "wiki-tactics", "Wikipedia: Chess tactics", "https://en.wikipedia.org/wiki/Chess_tactics"),
        _starter_source(subject, "wiki-fork", "Wikipedia: Fork (chess)", "https://en.wikipedia.org/wiki/Fork_(chess)"),
        _starter_source(subject, "wiki-pin", "Wikipedia: Pin (chess)", "https://en.wikipedia.org/wiki/Pin_(chess)"),
        _starter_source(subject, "wiki-back-rank", "Wikipedia: Back-rank checkmate", "https://en.wikipedia.org/wiki/Back-rank_checkmate"),
    ]
    nodes = [
        Node("fork", "The Fork", "A single piece attacks two or more enemy targets at once.",
             misconceptions=[Misconception("chess-fork-m1", "Only a knight can deliver a fork.")],
             grounding_corpus=[f"{subject}-wiki-fork", f"{subject}-wiki-tactics"], oracle="chess_fork",
             covers_keywords=["fork", "double attack"]),
        Node("pin", "The Pin", "A piece cannot move without exposing a more valuable piece or king behind it along an attack line.",
             prereqs=["fork"], misconceptions=[Misconception("chess-pin-m1", "A pinned piece can always move legally if it captures something.")],
             grounding_corpus=[f"{subject}-wiki-pin", f"{subject}-wiki-tactics"], oracle="chess_pin",
             covers_keywords=["pin", "pinned"]),
        Node("back-rank", "Back-Rank Checkmate", "A rook or queen checkmates a king trapped behind its own pieces on the back rank.",
             prereqs=["pin"], misconceptions=[Misconception("chess-back-rank-m1", "Back-rank mate always requires a sacrifice first.")],
             grounding_corpus=[f"{subject}-wiki-back-rank", f"{subject}-wiki-tactics"], oracle="chess_back_rank_mate",
             covers_keywords=["back-rank", "checkmate"]),
    ]
    return _starter_spec(subject, "Chess Tactics — forks, pins, and back-rank geometry",
                         "beginner chess", "three foundational tactical patterns", ["legal moves and piece movement"],
                         "Recognize tactical geometry and verify fixed positions with a legal move library.",
                         corpus, nodes, "python-chess", "Python + python-chess; fixed FEN positions only")


def generate_history() -> CurriculumSpec:
    """History starter: T2 citations and worked examples, deliberately no T3."""
    subject = "history"
    corpus = [
        _starter_source(subject, "gutenberg-newton", "Project Gutenberg: Newton's Principia", "https://www.gutenberg.org/cache/epub/28233/pg28233.txt"),
        _starter_source(subject, "ocw-history", "MIT OpenCourseWare: history course materials", "https://ocw.mit.edu/search/?q=history"),
        _starter_source(subject, "wiki-industrial-revolution", "Wikipedia: Industrial Revolution", "https://en.wikipedia.org/wiki/Industrial_Revolution"),
        _starter_source(subject, "wiki-historical-method", "Wikipedia: Historical method", "https://en.wikipedia.org/wiki/Historical_method"),
    ]
    nodes = [
        Node("primary-source", "Primary Sources", "A primary source is evidence produced during or close to the historical period under study.",
             misconceptions=[Misconception("history-primary-m1", "A primary source is automatically unbiased and complete.")],
             grounding_corpus=[f"{subject}-wiki-historical-method", f"{subject}-gutenberg-newton"], oracle=None,
             covers_keywords=["source", "evidence"]),
        Node("industrial-revolution", "Industrial Revolution", "The Industrial Revolution describes major changes in production, technology, labor, and society beginning in Britain and spreading more widely.",
             prereqs=["primary-source"], misconceptions=[Misconception("history-industrial-m1", "Industrialization was a single event with one universal date.")],
             grounding_corpus=[f"{subject}-wiki-industrial-revolution", f"{subject}-ocw-history"], oracle=None,
             covers_keywords=["industrial", "revolution"]),
        Node("historical-argument", "Historical Arguments", "A historical argument connects a claim to contextualized evidence while acknowledging source limits and alternative interpretations.",
             prereqs=["primary-source"], misconceptions=[Misconception("history-argument-m1", "A quotation by itself proves a historical conclusion.")],
             grounding_corpus=[f"{subject}-wiki-historical-method", f"{subject}-gutenberg-newton"], oracle=None,
             covers_keywords=["evidence", "history"]),
    ]
    return CurriculumSpec(
        subject=subject, title="History — sources, context, and arguments",
        scope={"depth": "primary sources through evidence-based historical arguments",
               "level": "introductory humanities", "assumed_prereqs": ["reading comprehension"],
               "goal": "evaluate historical claims without pretending a deterministic T3 exists"},
        tiers={"canonical": "t1", "corpus": "t2", "oracle": None, "assessment": "t4"},
        corpus=corpus, oracle={}, nodes=nodes,
        generator_note="Honest no-T3 starter: worked examples and citations only; no executable answer key is claimed.",
    )
