SELECT c.ts, t.name, c.value
FROM counter c
JOIN counter_track t ON c.track_id = t.id
WHERE t.name = 'VIRTUAL-SKIN-CPU-temp'
ORDER BY c.ts
LIMIT 5;
