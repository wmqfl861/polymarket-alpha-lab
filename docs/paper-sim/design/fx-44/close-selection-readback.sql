-- FX-44 diagnostic for a managed store with the six pre-registration migrations.
-- Run this single SELECT in a read-only session. It returns one row per actual
-- paper entry in the criterion C denominator. For each (model_key,
-- market_type, gate_version), count rows with a nonnull legacy_close_price
-- for the current evaluation's coverage numerator.
--
-- The window columns compare stored observations against their stored boundary.
-- They cannot establish the source of that boundary, a request's receipt time,
-- the token bought, or membership of a future registration barrier. An existing
-- close can also retain historical eligibility under that barrier. Registered
-- qualification remains unknown for every row in this readback.
SELECT
    c.model_name || ':' || c.model_version AS model_key,
    coalesce(nullif(c.market_type, ''), c.market_kind) AS market_type,
    s.gate_version,
    c.candidate_id,
    e.entry_id,
    c.side AS buy_side,
    e.price AS fill_price,
    c.event_start_at AS stored_boundary,
    legacy.evidence_id AS legacy_evidence_id,
    legacy.side AS legacy_side,
    legacy.close_price AS legacy_close_price,
    CASE WHEN legacy.close_price IS NOT NULL
         THEN legacy.observed_at END AS legacy_price_observed_at,
    matched.evidence_id AS window_evidence_id,
    matched.last AS window_last,
    matched.mid AS window_mid,
    matched.close_price AS window_price,
    matched.observed_at AS window_observed_at,
    CASE
        WHEN c.event_start_at IS NULL THEN 'NO_STORED_BOUNDARY'
        WHEN matched.evidence_id IS NULL THEN 'NO_WINDOW_MATCH'
        ELSE 'UNVERIFIED_WINDOW_MATCH'
    END AS window_status
FROM papersim.entries AS e
JOIN papersim.candidates AS c ON c.candidate_id = e.candidate_id
JOIN papersim.scan_runs AS s ON s.scan_id = c.scan_id
LEFT JOIN LATERAL (
    SELECT p.evidence_id, p.observed_at, p.side, coalesce(p.last, p.mid) AS close_price
    FROM papersim.candidate_price_evidence AS p
    WHERE p.candidate_id = c.candidate_id AND p.evidence_role = 'CLOSE'
    ORDER BY p.evidence_id DESC
    LIMIT 1
) AS legacy ON true
LEFT JOIN LATERAL (
    SELECT p.evidence_id, p.observed_at, p.last, p.mid, coalesce(p.last, p.mid) AS close_price
    FROM papersim.candidate_price_evidence AS p
    WHERE p.candidate_id = c.candidate_id AND p.evidence_role = 'CLOSE'
      AND p.side = c.side AND coalesce(p.last, p.mid) IS NOT NULL
      AND p.observed_at >= c.event_start_at - interval '60 minutes'
      AND p.observed_at < c.event_start_at
    ORDER BY p.observed_at DESC, p.evidence_id DESC
    LIMIT 1
) AS matched ON true
WHERE c.conclusion = 'ACCEPTED'
ORDER BY c.candidate_id;
