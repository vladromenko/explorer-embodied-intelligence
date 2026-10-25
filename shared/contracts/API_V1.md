# Explorer API v1 — frozen integration surface

All JSON responses use UTF-8. Authenticated same-origin browser sessions for NX; node bearer tokens for agents. POST requires Origin/CSRF for browser auth. No physical endpoint is armed by default.

GET /api/state => {schema_version:1,nodes:[{id,name,host,online,last_seen,health,capabilities,cameras}],experiments:[{id,title,status,summary,blockers}],jobs:[],skills:[],research:{},storage:{},admission:{physical_enabled:false,reasons:[]}}
GET /api/jobs => {jobs:[]} ; POST /api/jobs {kind,project_id,config:{}} => job
POST /api/jobs/{id}/{pause,resume,cancel}
GET /api/research => registry counts, hypotheses, runs, status
GET /api/skills => {skills:[]}
GET /api/recordings => {recordings:[]} ; POST /api/recordings {camera_id,project_id,duration_s} => job (camera-only observation recording, no actuator calls)
GET /api/cameras/{camera_id}/snapshot ; GET /api/cameras/{camera_id}/stream
GET /api/simulations/{job_id}/frame
POST /api/events => idempotent durable node event admission {event_id,node_id,run_id,project_id,schema_version,wall_utc,monotonic_ns,clock_domain,payload,sha256}
POST /api/stop => latch all new control sources disarmed; legacy bypass remains explicit admission blocker.
GET /api/health => {ok:true,version,commit}
GET /api/operator-actions => {actions:[]} (honest required actions, no false scientific success)
GET /api/login ; POST /api/login ; POST /api/logout

Agent v1 on each node: GET /health, GET /inventory, GET /cameras, GET /cameras/{id}/snapshot, GET /cameras/{id}/stream, POST /recordings {camera_id,duration_s,recording_id}; all require EXPLORER_NODE_TOKEN except minimal /health. GET /recordings/{id}, GET /recordings/{id}/manifest, GET /recordings/{id}/download.
Agent inventory returns {node_id,hostname,wall_utc,monotonic_ns,clock_domain,hardware:{},cameras:[{id,label,...}],serial_devices:[],calibrations:[],capabilities:{},errors:[]}.
Agents bind local LAN :8091 (Pi) and :8092 (Nano), no physical actuation capabilities.

Camera IDs: pi-overview, nano-rgbd, nano-logitech. Agents own camera capture singleton, retain monotonic exposure/receive distinction; estimated receive timestamps labelled as such.
Actions ROSMASTER v1: [base,shoulder,elbow,wrist_pitch,wrist_roll,gripper,vx,vy,wz]. Arm unit degrees after audited profile; chassis m/s,m/s,rad/s. First five coordinates+gripper distinct from base velocity. No measured joint feedback assumed.

No new source code may contain Python `continue` or JS continue statement. All node deployment/network/DB/gateway changes belong to root integrator.
