from dataclasses import replace
from types import SimpleNamespace
import queue
import pytest
from simulation_protocol.dto import SimulationSnapshot, VehicleRuntimeSnapshot
from simulation_protocol.codec import dumps_snapshot
from simulation_protocol.client import RedisSnapshotSubscription
from simulation_protocol.snapshot_journal import append_snapshot, JournalSubscription
from traffic_eval.collector import TrafficMetricsCollector


def frame(n):
    return SimulationSnapshot('test', 'RUNNING', n, float(n), 10, 0, '', vehicles=(VehicleRuntimeSnapshot('v',0,0,0,0,'r','l',waiting_time=n,distance=n,hard_braking_events=n),))


def test_redis_does_not_replay_older_pubsub_after_latest_fallback():
    messages=iter([None, {'type':'message','data':dumps_snapshot(frame(2))}, {'type':'message','data':dumps_snapshot(frame(3))}, {'type':'message','data':dumps_snapshot(frame(4))}])
    snapshots=iter([frame(1),frame(3)])
    manager=SimpleNamespace(_store=SimpleNamespace(pubsub=lambda _:SimpleNamespace(get_message=lambda **kw:next(messages))),snapshot=lambda _:next(snapshots))
    sub=RedisSnapshotSubscription(manager,'test')
    assert [sub.get(1).sequence for _ in range(3)] == [1,3,4]


def test_metrics_reject_old_frames_before_membership_mutation():
    def run(order):
        c=TrafficMetricsCollector('fixed')
        for n in order:c.observe_snapshot(frame(n))
        return c._tracker._accum, c._tracker.scene_hard_braking_events
    assert run([1,2,3]) == run([1,2,3,2,3])


def test_journal_replays_complete_order_after_consumer_delay_and_restart(tmp_path):
    for n in range(1,11):append_snapshot(tmp_path,frame(n))
    append_snapshot(tmp_path,frame(5))
    manager=SimpleNamespace(snapshot=lambda _:frame(10))
    for _ in range(2):
        sub=JournalSubscription(manager,'test',tmp_path)
        assert [sub.get(.1).sequence for _ in range(10)]==list(range(1,11))
        with pytest.raises(queue.Empty):sub.get(.01)
        sub.close()


def test_journal_delivers_queued_cancellation_without_file(tmp_path):
    stopped=replace(frame(1),state='STOPPED')
    sub=JournalSubscription(SimpleNamespace(snapshot=lambda _:stopped),'test',tmp_path)
    assert sub.get(.1)==stopped

def test_legacy_same_sequence_terminal_is_not_lost(tmp_path):
    running=frame(1);terminal=replace(running,state='COMPLETED')
    manager=SimpleNamespace(snapshot=lambda _:terminal)
    append_snapshot(tmp_path,running)
    sub=JournalSubscription(manager,'test',tmp_path)
    assert sub.get(.1).state=='RUNNING'
    assert sub.get(.1).state=='COMPLETED'
    messages=iter([{'type':'message','data':dumps_snapshot(terminal)}])
    redis_manager=SimpleNamespace(_store=SimpleNamespace(pubsub=lambda _:SimpleNamespace(get_message=lambda **kw:next(messages))),snapshot=lambda _:running)
    stream=RedisSnapshotSubscription(redis_manager,'test')
    assert stream.get(.1).state=='RUNNING'
    assert stream.get(.1).state=='COMPLETED'
