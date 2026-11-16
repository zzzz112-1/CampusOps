"""Current equipment state is distinct from a historical incident record."""
from campusops.ui.presentation import equipment_name,local_time,EQUIPMENT_LABELS


def current_incidents(incidents,equipment,archived=frozenset()):
    state={row.id:row.status for row in equipment}
    return tuple(row for row in incidents if row.status=='OPEN' and row.equipment_id not in archived
                 and state.get(row.equipment_id) in ('FAULTED','MAINTENANCE'))


def incident_label(incident,equipment,archived=frozenset()):
    current=next((row for row in equipment if row.id==incident.equipment_id),None)
    state='已移除' if incident.equipment_id in archived else EQUIPMENT_LABELS.get(current.status,'未找到') if current else '未找到'
    status='已解决' if incident.status=='RESOLVED' else '待核对' if current is None or current.status=='AVAILABLE' else '未解决'
    return f'{equipment_name(incident.equipment_id,equipment)} · {status} · 生效 {local_time(incident.effective_at)} · 记录 {incident.id[-8:]} · 当前{state}'
