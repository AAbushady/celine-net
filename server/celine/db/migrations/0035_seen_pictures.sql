-- The pictures a result takes to the model with it are one list on its display, `see`
-- (turns/context.py calls_wire), each {id, filename, sha256, media_type}. A browser screen
-- meant to be seen said `see: true` beside its `shot`: its `see` names that shot now.
UPDATE tool_calls
   SET display = json_set(display, '$.see', json_array(json(json_extract(display, '$.shot'))))
 WHERE json_valid(display) AND json_type(display, '$.see') = 'true'
   AND json_type(display, '$.shot') = 'object';
UPDATE tool_calls SET display = json_remove(display, '$.see')
 WHERE json_valid(display) AND json_type(display, '$.see') IN ('true', 'false');
