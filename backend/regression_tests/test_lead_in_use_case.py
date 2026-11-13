import pytest
from unittest.mock import Mock

def test_record_commit_failure_propagates_and_rolls_back():
    from app.application.lead_in import records
    db=Mock()
    db.commit.side_effect=RuntimeError('commit failed')
    from datetime import datetime
    record=Mock(id=12, updated_at=datetime.now())
    from unittest.mock import patch
    with patch.object(records,'_upsert_record',return_value=record), patch.object(records,'write_audit_log'):
        with pytest.raises(RuntimeError,match='commit failed'):
            from app.application.use_cases.task_lead_in import TaskLeadInUseCase
            TaskLeadInUseCase(db)._persist_result(db,task_id=1,drive_type='quark',account_name='a',dir_path='/x',material_name='m',material_fid='f',status='added',message='ok',config_version=1,actor_user_id=None,ip=None,user_agent=None)
    db.rollback.assert_called_once()

