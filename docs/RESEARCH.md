# P4 research notes

This workstream uses deterministic library executions as T3 answer keys. The
designer never treats model prose as truth, and source discovery never converts
metadata or an abstract into article text.

## Primary contracts consulted

- Python `urllib.request` and `ipaddress` define the bounded HTTP request and
  special/private address checks used by `open_tutor/network.py`:
  <https://docs.python.org/3/library/urllib.request.html> and
  <https://docs.python.org/3/library/ipaddress.html>.
- The OpenAI-compatible local transport is the documented Chat Completions
  request shape, restricted here to a loopback endpoint and JSON-only response:
  <https://github.com/ggml-org/llama.cpp/tree/master/examples/server>.
  No remote OpenAI/cloud endpoint is used by this workstream.
- SymPy’s differentiation/integration APIs are used for fixed expressions:
  <https://docs.sympy.org/latest/tutorials/intro-tutorial/calculus.html>.
- music21’s scale, chord, pitch, and interval objects are used for fixed music
  examples: <https://music21.org/music21docs/>.
- RDKit’s molecule and descriptor APIs are used for fixed trusted SMILES:
  <https://www.rdkit.org/docs/GettingStartedInPython.html>.
- python-chess’s board legality, attacks, pin, and checkmate APIs are used for
  fixed FEN positions: <https://python-chess.readthedocs.io/en/latest/>.
- mypy is invoked as the actual static type checker on a fixed trusted snippet;
  annotation strings are not inspected: <https://mypy.readthedocs.io/en/stable/>.
- Qiskit `Statevector` remains the quantum deterministic oracle boundary:
  <https://docs.quantum.ibm.com/api/qiskit/qiskit.quantum_info.Statevector>.

These references describe the library/API semantics. The verifier’s
`MIN_GROUNDING_CHARS = 2500` rule remains the independent grounding gate and
was not changed.

## Source adapter decisions

Wikidata discovery validates entity IDs (`Q...`) and retrieves the entity JSON;
Gutenberg uses a small catalog of known eBook IDs and downloads the complete
plain-text file; OCW records canonical course pages; arXiv validates old and
new paper-ID forms and exposes the PDF URL. The arXiv adapter extracts PDF
pages only when a PDF parser is installed and reports a named error otherwise.
It never presents the arXiv abstract as full text.

All adapter URLs pass the network validator. Redirects are followed manually
with a maximum count, each destination is checked again, DNS answers are
rejected when they resolve to private/special addresses, and bodies are bounded
before decoding. Browser rendering applies the same URL checks to subrequests.

## Subject/oracle notes

The chemistry starter uses RDKit only for fixed, trusted `CCO` and caffeine
SMILES; the model cannot submit executable chemistry code. The type-checker
starter uses one repository-owned snippet in a temporary directory; arbitrary
model or learner snippets are outside this trusted registry and are rejected.
History declares `oracle: null` and uses citations/worked examples only. No
subject’s generated candidate can set a grounded or verification field.
