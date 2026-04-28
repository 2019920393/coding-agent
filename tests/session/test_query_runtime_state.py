from codo.query import QueryState

def test_query_state_exposes_phase_checkpoint_and_interaction_fields():
    state = QueryState(messages=[])

    assert state.phase == "prepare_turn"
    assert state.pending_interaction is None
    assert state.checkpoint_id is None
    assert state.active_tool_ids == []
    assert state.active_agent_id is None
