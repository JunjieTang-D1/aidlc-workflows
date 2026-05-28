# Integration Verification Plan

## Trigger

This plan executes AFTER all parallel units (auth-service, product-catalog, notification-service) are merged to `main` and BEFORE the api-gateway unit begins.

## Verification Steps

### 1. Combined Build

```bash
# All three services must build without errors
npm run build --workspace=auth-service
npm run build --workspace=product-catalog
npm run build --workspace=notification-service
```

**Pass criteria**: Zero build errors across all workspaces.

### 2. Unit Tests

```bash
npm run test --workspace=auth-service
npm run test --workspace=product-catalog
npm run test --workspace=notification-service
```

**Pass criteria**: All existing + new unit tests pass (100% green).

### 3. Contract Tests

```bash
# Verify each service implements its declared interface correctly
npm run test:contracts
```

**Pass criteria**: All interface contracts from `aidlc-docs/contracts/interfaces.md` are satisfied.

### 4. Integration Tests

```bash
# Spin up all services, run cross-service test suite
docker-compose -f docker-compose.test.yml up -d
npm run test:integration
docker-compose -f docker-compose.test.yml down
```

**Pass criteria**: All cross-service communication works as specified in contracts.

### 5. No Regressions

Compare test results against pre-parallel baseline:
- No previously-passing test now fails
- No new lint warnings introduced
- No security scan regressions

## Failure Attribution

If integration fails:

1. Identify which service's tests/contracts fail
2. Assign fix to that service's original agent (or Agent 4)
3. Fix must be applied on the service's feature branch
4. Re-merge and re-run integration verification

## Completion

Integration verification is a **hard gate** — the api-gateway unit MUST NOT begin until all steps pass.

Log result in `aidlc-docs/audit.md`:
```
[TIMESTAMP] MULTI-AGENT-07: Integration verification PASSED/FAILED
  - Build: PASS/FAIL
  - Unit tests: PASS/FAIL (X/Y)
  - Contract tests: PASS/FAIL
  - Integration tests: PASS/FAIL (X/Y)
  - Regressions: NONE/LIST
```
