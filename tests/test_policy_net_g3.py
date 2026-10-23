"""G3 shapes, deterministic training, observable pruning and artifact isolation."""
import builtins
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import types

import numpy as np
import pytest
import torch

from palca.learning.policy_net import (PolicyNetG3, cached_policy_net, policy_tensors,
                                       predict_policy_batch, save_policy_net, ENVELOPE_MM3)
from palca.integration.policy_packer_sched import PolicyPackerScheduler
from palca.scheduler.scheduler_v1 import SchedulerConfig, SchedulerRampState, SchedulerSimState
from palca.domain.box import Box

ROOT = Path(__file__).resolve().parents[1]
BASELINE_REV = '7c295de816671c124e02c4ba52c569a872c94e73'


def trainer():
    spec = importlib.util.spec_from_file_location('train_g3_test', ROOT/'scripts/train_policy_g3.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample(b=2, c=8):
    rng = np.random.default_rng(39)
    actions = np.tile([0, 20, 20, 0, 400, 300, 200], (b,c,1)).astype(np.int32)
    boxes = np.zeros((b,15,4), np.float32)
    boxes[:, :3] = [[.4,.3,.2,1], [.5,.3,.2,1], [.7,.4,.2,1]]
    return dict(height=rng.integers(0, 500, (b,62,42), dtype=np.uint16), boxes=boxes,
                scalars=np.zeros((b,4), np.float32), cand_actions=actions,
                cand_height_after=rng.integers(500,1000,(b,c,62,42),dtype=np.uint16),
                cand_count=np.full(b,c,dtype=np.int16))


@pytest.fixture
def checkpoint(tmp_path):
    path = tmp_path/'policy.pt'
    torch.manual_seed(39)
    save_policy_net(PolicyNetG3(), path, seeds={}, data={'forbidden': '/never/open/data'}, metrics={})
    return path


def test_shapes_invariance_and_batch():
    torch.set_num_threads(1)
    model = PolicyNetG3().eval()
    assert sum(p.numel() for p in model.parameters()) <= 300000
    batch = sample()
    batch['cand_count'][1] = 3
    prediction = predict_policy_batch(model, batch)
    assert prediction.shape == (2,8)
    np.testing.assert_allclose(prediction.sum(-1), 1, rtol=0, atol=1e-6)
    assert (prediction[1,3:] == 0).all()
    for i in range(2):
        single = {k: v[i:i+1] for k,v in batch.items()}
        np.testing.assert_allclose(predict_policy_batch(model,single)[0], prediction[i], atol=1e-6)
    batch['boxes'] = batch['boxes'][:, ::-1].copy()
    np.testing.assert_array_equal(predict_policy_batch(model,batch), prediction)
    # Removing the picked box leaves the same remaining set as explicitly removing it.
    original = model(*policy_tensors(batch)).detach()
    batch['boxes'][:, -1] = 0
    torch.testing.assert_close(model(*policy_tensors(batch)), original)


def test_soft_loss_nan_and_units():
    module = trainer()
    logits = torch.tensor([[1.,2.,3.,-torch.inf]], requires_grad=True)
    values = torch.tensor([[.5,.505,float('nan'),float('nan')]])*ENVELOPE_MM3
    valid = torch.tensor([[True,True,True,False]])
    loss = module.policy_loss(logits,values,torch.tensor([1]),valid)
    expected = -(torch.softmax(torch.tensor([100.,101.]),0)*logits[0,:2].log_softmax(0)).sum()
    expected += .5*torch.nn.functional.cross_entropy(logits,torch.tensor([1]))
    torch.testing.assert_close(loss,expected)
    loss.backward()
    assert torch.isfinite(logits.grad).all()


def test_training_deterministic_two_episodes(tmp_path):
    module = trainer()
    data = tmp_path/'data'
    data.mkdir()
    manifest = dict(encoding_version='g1-v1',policy='official_b150',prior='mix',with_after=True,
        arguments=dict(policy='official_b150',prior='mix',with_after=True),
        configuration_sha256={'scripts/gen_value_data.py':'fixture'},
        source_sha256={'src/palca/learning/synthetic_flows.py':'fixture'})
    (data/'manifest.json').write_text(json.dumps(manifest))
    names = [f'ep_mix_{i}.npz' for i in range(100) if module.episode_split(f'ep_mix_{i}.npz')=='train'][:2]
    for name in names:
        row = sample(2,64)
        row['cand_count'][:] = 3
        row['cand_values'] = np.full((2,64),np.nan,np.float32)
        row['cand_values'][:,:3] = np.array([.5,.505,.502])*ENVELOPE_MM3
        row['cand_chosen'] = np.ones(2,np.int16)
        np.savez_compressed(data/name,**row)
    for i in range(2):
        module.main(['--data',str(data),'--out',str(tmp_path/f'model{i}'),
                     '--epochs','2','--batch','2','--device','cpu'])
    a,b = [cached_policy_net(tmp_path/f'model{i}/policy_g3.pt') for i in range(2)]
    for k,v in a.state_dict().items():
        torch.testing.assert_close(v,b.state_dict()[k],rtol=0,atol=0)
    manifest['with_after'] = False
    (data/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='D-032'):
        module.validate_source(data)


def policy(path='', **overrides):
    params = dict(lookahead_k=15, overhang_mm=20, stacking_mode='heightfield',
        stability_mode='off', time_budget_ms=0, micro_plan_enabled=True,
        micro_plan_node_budget=57, micro_plan_hard_cap_ms=600000, micro_plan_depth=2,
        micro_plan_value='rollout', micro_plan_rollout_leaves=2, rollout_future_boxes=6,
        rollout_prior='generic', policy_prune_model_path=str(path), policy_prune_depths='all')
    params.update(overrides)
    return PolicyPackerScheduler.from_defaults(**params)


def flow():
    rng = random.Random(283)
    return [Box(i,rng.choice((410,470,530)),rng.choice((270,310,370)),
                rng.choice((230,290,350)),0,destination=1) for i in range(130)]


def replay(policies, flows, stop):
    queues = [list(f[:15]) for f in flows]
    upstream = [list(f[15:]) for f in flows]
    pallets = [p._new_pallet() for p in policies]
    picks = closures = 0
    while queues[0] == queues[1] and picks < stop:
        plans=[]
        for i,p in enumerate(policies):
            state=SchedulerSimState(picks,{1:queues[i]},{1:pallets[i]},set(),
                ramp_states={1:SchedulerRampState(tuple(queues[i]),tuple(upstream[i]),15)})
            plans.append(p._scheduler.choose_action(state))
        assert plans[0] == plans[1], picks
        if plans[0] is None:
            assert pallets[0].placements
            pallets = [p._new_pallet() for p in policies]
            closures += 1
            assert closures < 30
            continue
        for i,plan in enumerate(plans):
            pallets[i].commit_place(plan.preview)
            queues[i].pop(plan.buffer_index)
            if upstream[i]:
                queues[i].append(upstream[i].pop(0))
        picks += 1
    return picks


def test_large_k_equivalence_and_batched_call(checkpoint,monkeypatch):
    p = policy(checkpoint, policy_prune_k=10000)
    sizes=[]
    def capture(model, inputs, output):
        sizes.append(inputs[0].shape[0]*inputs[3].shape[1])
    handle=p._scheduler._g3_model.register_forward_hook(capture)
    try:
        assert replay([policy(),p],[flow(),flow()],15)==15
    finally:
        handle.remove()
    assert max(sizes)>1
    assert all(t>0 for t in p._scheduler.g3_network_times_ms)
    assert p.collect_kpis()['policy_prune_network_ms_mean']>0


def test_d022_prefix_100_permuted_suffix(checkpoint):
    original=flow()
    changed=original[:100]+list(reversed(original[100:]))
    assert replay([policy(checkpoint,policy_prune_k=4,micro_plan_depth=1) for _ in range(2)],
                  [original,changed],100)==86


def test_d032_only_checkpoint_json(checkpoint,monkeypatch):
    import palca.learning.policy_net as net
    net._PATH_CACHE.clear()
    net._MODEL_CACHE.clear()
    allowed={checkpoint.resolve(),checkpoint.with_suffix('.json').resolve()}
    opened=[]
    def guard(original):
        def checked(file,*a,**kw):
            if not isinstance(file,int):
                assert Path(file).resolve() in allowed
                opened.append(Path(file).resolve())
            return original(file,*a,**kw)
        return checked
    monkeypatch.setattr(builtins,'open',guard(builtins.open))
    monkeypatch.setattr(io,'open',guard(io.open))
    a,b=policy(checkpoint),policy(checkpoint)
    assert a._scheduler._g3_model is b._scheduler._g3_model
    assert replay([a,b],[flow(),flow()],2)==2
    assert set(opened)==allowed
    assert opened.count(checkpoint.with_suffix('.json').resolve())==1


def test_cache_sha_and_validation(checkpoint):
    model=cached_policy_net(checkpoint)
    assert cached_policy_net(checkpoint) is model
    metadata=json.loads(checkpoint.with_suffix('.json').read_text())
    metadata['sha256']='0'*64
    checkpoint.with_suffix('.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError,match='SHA256'):
        cached_policy_net(checkpoint)
    for overrides in ({'policy_prune_k':0},{'policy_prune_device':'x'},{'policy_prune_depths':'x'}):
        with pytest.raises(ValueError,match='policy_prune'):
            SchedulerConfig(**overrides)
    from sim.run import build_parser
    args=build_parser().parse_args(['--excel','unused.xlsx','--policy-prune-k','4',
        '--policy-prune-model-path','~/policy.pt','--policy-prune-depths','all'])
    assert args.policy_prune_k==4 and args.policy_prune_depths=='all'


@pytest.mark.skipif(os.environ.get('G3_FULL_EQUIVALENCE')!='1',reason='director: full canonical dumps')
@pytest.mark.parametrize('seed',[50021,1])
def test_default_complete_dump(seed,tmp_path,monkeypatch):
    import palca.integration.policy_packer_sched as integration
    from sim.run import run_simulation
    from palca.study.params import preset_params
    import yaml
    config=next(c for c in yaml.safe_load((ROOT/'configs/sweeps/computo_eval.yaml').read_text())
                if c['name']=='computo_b150_l4')
    source=subprocess.check_output(['git','--no-pager','show',
        f'{BASELINE_REV}:src/palca/scheduler/scheduler_v1.py'],text=True)
    baseline=types.ModuleType('palca.scheduler._g3_reference')
    baseline.__package__='palca.scheduler'
    monkeypatch.setitem(sys.modules,baseline.__name__,baseline)
    exec(compile(source,'<baseline>','exec'),baseline.__dict__)
    params=preset_params(config['preset'],**config['overrides'])
    params['excel_path']=str(ROOT/params['excel_path'])
    def normalize(x):
        if isinstance(x,dict):
            return {k:normalize(v) for k,v in x.items() if k!='git_head' and 'time' not in k and not k.endswith('_ms')}
        return [normalize(v) for v in x] if isinstance(x,list) else x
    dumps=[]
    for legacy in (False,True):
        if legacy:
            monkeypatch.setattr(integration,'SchedulerV1',baseline.SchedulerV1)
        path=tmp_path/'dump.json'
        run_simulation(**dict(params,out_path=None,episode_seed=seed,dump_placements_path=str(path)))
        dumps.append(normalize(json.loads(path.read_text())))
    assert dumps[0]==dumps[1]


def test_sweep():
    import yaml
    configs=yaml.safe_load((ROOT/'configs/sweeps/g3_tune.yaml').read_text())
    base=next(c for c in yaml.safe_load((ROOT/'configs/sweeps/computo_eval.yaml').read_text())
              if c['name']=='computo_b150_l4')
    assert configs[0]['overrides']==base['overrides'] and configs[0]['role']=='official'
    assert len(configs)==6 and all(c['role']=='control' for c in configs[1:])


def test_prune_depths_and_stable_ties(checkpoint, monkeypatch):
    # All-equal logits must retain the first k candidates in the current order.
    model=cached_policy_net(checkpoint)
    def zeros(height,boxes,scalars,actions,after,valid):
        return torch.zeros_like(valid,dtype=torch.float32)
    monkeypatch.setattr(model,'forward',zeros)
    for depths in ('root','all'):
        p=policy(checkpoint,policy_prune_depths=depths,policy_prune_k=2,
                 micro_plan_value='score',rollout_future_boxes=0,micro_plan_depth=3,
                 micro_plan_node_budget=150)
        engine=p._scheduler
        calls=[]
        prune=engine._prune_policy
        def capture(node,expansions,actions):
            kept=prune(node,expansions,actions)
            assert all(a is b for a,b in zip(kept,expansions[:2]))
            assert len(kept)==min(2,len(expansions))
            calls.append(node.placed_count)
            return kept
        monkeypatch.setattr(engine,'_prune_policy',capture)
        state=SchedulerSimState(0,{1:flow()[:15]},{1:p._new_pallet()},set())
        assert engine.choose_action(state) is not None
        assert calls and calls[0]==0
        assert (set(calls)=={0}) if depths=='root' else max(calls)>0


def test_simulation_dump(checkpoint,tmp_path):
    import pandas as pd
    from sim.run import run_simulation
    excel=tmp_path/'flow.xlsx'
    pd.DataFrame({'timestamp':[0]*3,'destino':[1]*3,'largo':[400]*3,
                  'ancho':[300]*3,'alto':[200]*3}).to_excel(excel,index=False)
    dump=tmp_path/'dump.json'
    result=run_simulation(excel_path=str(excel),out_path=None,model='M1',n_per_pallet=24,
        t_pick_place=1,staging_cap=0,policy='palca',lookahead_k=15,arrival_mode='immediate',
        stacking_mode='heightfield',overhang_mm=20,stability_mode='off',time_budget_ms=0,
        micro_plan=True,micro_plan_node_budget=57,policy_prune_model_path=str(checkpoint),
        policy_prune_k=2,policy_prune_depths='all',dump_placements_path=str(dump))
    params=json.loads(dump.read_text())['params']
    assert params['policy_prune_sha256']==cached_policy_net(checkpoint).metadata['sha256']
    assert params['policy_prune_k']==2 and params['policy_prune_depths']=='all'
    assert result['metrics']['pallet_kpis']['policy_prune_network_ms_mean']>0
