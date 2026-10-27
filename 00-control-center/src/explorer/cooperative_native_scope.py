"""ENGINEERING_GLUE: extract only a genuinely returned native giver scope.

The native policy resume reply is already a full scope at its outer level;
its nested ``native`` contains merely the original ManualTeleop acknowledgement.
Never replace a returned epoch/generation with a later read of the source.
"""


def returned_giver_scope(pair,result,operation):
    candidate=pair['source']['native'] if operation=='native_attach' else result.get('begin',result)
    for _ in range(4):
        if not isinstance(candidate,dict):break
        epoch=candidate.get('control_epoch');active=candidate.get('active');generation=candidate.get('owner_generation')
        if (type(generation) is int and generation>=0 and type(active) is bool and
                (isinstance(epoch,str) and 0<len(epoch)<=160 or active is False and epoch is None)):
            return candidate
        candidate=candidate.get('native') or (candidate.get('prepare') or {}).get('native')
    raise ValueError('Exact returned native giver epoch/generation scope unavailable')
