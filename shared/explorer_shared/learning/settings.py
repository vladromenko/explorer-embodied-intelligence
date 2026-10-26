"""ENGINEERING_GLUE: Experiment-neutral ACT numeric settings; no simulator options or imports."""


def act_parameters(config):
    def integer(name, default, lower, upper):
        value=config.get(name,default)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or int(value)!=value or not lower<=value<=upper:
            raise ValueError('Invalid ACT parameter: '+name)
        return int(value)
    device=config.get('device','cpu')
    if device not in ('cpu','cuda'):raise ValueError('ACT device must be cpu or cuda')
    return dict(seed=integer('seed',41,0,1_000_000),act_steps=integer('act_steps',120,0,100_000),
        batch_size=integer('batch_size',8,1,64),image_size=integer('image_size',64,32,224),
        chunk_size=integer('chunk_size',8,1,100),device=device)
