-- Strict information barrier: no record first available at the decision tick.
-- A newer observation takes precedence over any later revision of an older one.
-- The LEFT JOIN preserves missing requested features as NULL, never as zero.
-- Revision identity and availability ordering are validated before this query.
WITH ranked AS (
    SELECT d.decision_id, d.at, f.feature,
           o.version_id, o.observed_at, o.available_at, o.revision, o.value,
           ROW_NUMBER() OVER (
               PARTITION BY d.decision_id, f.feature
               ORDER BY o.observed_at DESC, o.revision DESC
           ) AS choice
    FROM decisions AS d
    CROSS JOIN requested_features AS f
    LEFT JOIN observations AS o
      ON o.token = d.token
     AND o.feature = f.feature
     AND o.observed_at < d.at
     AND o.available_at < d.at
)
SELECT decision_id, feature, version_id, observed_at, available_at, revision, value
FROM ranked
WHERE choice = 1
ORDER BY at, decision_id, feature;
