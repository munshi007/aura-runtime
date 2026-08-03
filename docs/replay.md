# Deterministic replay

Aura replay answers a counterfactual question: what would a different policy conclude
about evidence that has already been captured?

It is not tool re-execution. The engine reads canonical events, evaluates AuraSpec in
their recorded order, and returns a report without modifying the database. This makes it
safe for production incident analysis and CI regression gates.

## Report semantics

- **introduced**: emitted by the replayed policy but absent from recorded findings;
- **resolved**: present in recorded findings but absent from replay;
- **unchanged**: identical policy and evidence conclusions in both sets;
- **policy hash**: SHA-256 over canonical AuraSpec content;
- **transcript integrity**: verification of the flight recorder's hash chain.

`--fail-on-new` returns exit code 2 when introduced findings exist, allowing CI to reject
a policy or agent change while still writing the complete JSON report.

## Run and manifest comparison

Run diff aligns normalized event sequences and reports edit ranges plus the first
divergence. Manifest diff compares the latest captured `tools/list` response for each run
and classifies tools as added, removed, changed, or unchanged.

