"""Chinese equipment management; persistence only through protected Main ports."""
from uuid import uuid4
import re
import streamlit as st
from pydantic import ValidationError
from campusops.contracts.models import EquipmentCreate,EquipmentUpdate,EquipmentArchiveCreate,EquipmentRestoreCreate
from campusops.contracts.ports import EquipmentCatalogPort
from campusops.contracts.errors import CampusOpsError
from campusops.ui.controller import UiController,UiInputError
from campusops.ui.presentation import CATEGORY_LABELS,CAPABILITY_LABELS,EQUIPMENT_LABELS,capabilities_label,html_text,safe_text,record_table_html

ACTIONS=("器材列表","新增器材","编辑器材","移除器材","已移除器材")


def activity():
    from campusops.security import streamlit_gate
    streamlit_gate.touch_user_activity()


def clear_view():
    activity()
    for key in ('catalog_preview','catalog_result','catalog_backup','catalog_revision','catalog_confirm','catalog_archive_actor','catalog_archive_reason'):
        st.session_state.pop(key,None)
    st.session_state['catalog_epoch']=st.session_state.get('catalog_epoch',0)+1


def open_catalog(action):
    clear_view();st.session_state['campusops_resources_tab']='器材管理';st.session_state['catalog_action']=action


def select_record():
    st.session_state['catalog_selected_id']=st.session_state.get('catalog_selected');clear_view()


def card(title,item,archived=False,status=None):
    st.html('<article class="campusops-card"><h4>'+html_text(title)+'</h4><p><strong>'+html_text(item.name)+
        '</strong></p><p>'+html_text(CATEGORY_LABELS.get(item.category,item.category))+' · '+html_text(capabilities_label(item.capabilities))+
        '</p><p class="muted">'+html_text('已移除（历史保留）' if archived else EQUIPMENT_LABELS.get(status or getattr(item,'status','AVAILABLE'),'待新增'))+'</p></article>',unsafe_allow_javascript=False)


class CatalogController(UiController):
    def dispatch(self,operation,request):
        intent=('catalog',operation,request.idempotency_key);self._begin(intent)
        self.state['catalog_pending']=(operation,request)
        try:result=getattr(self.service,{'create':'create_equipment','update':'update_equipment','archive':'archive_equipment','restore':'restore_equipment'}[operation])(request)
        except CampusOpsError:
            self.state.pop('catalog_pending',None)
            attempts=dict(self.state.get('campusops_command_attempts',{}));attempts.pop(intent,None);self.state['campusops_command_attempts']=attempts
            raise
        if self.service.find_equipment_command(request.idempotency_key)!=result:
            raise UiInputError('器材操作已发送，结果尚未核实。请查询已保存结果，不要重复提交。')
        self.state.pop('catalog_pending',None);self._confirmed(intent)
        return result


def render_catalog(parent):
    controller=CatalogController(parent.service,parent.state,read_only=parent.read_only)
    if not isinstance(controller.service,EquipmentCatalogPort):
        st.info('当前服务未提供器材管理，请维护者更新程序。');return
    entries=controller.service.list_equipment_catalog(True)
    st.subheader('器材台账管理')
    action=st.radio('器材操作',ACTIONS,horizontal=True,key='catalog_action',on_change=clear_view)
    st.caption('每台实物单独登记。新增、编辑、移除都不调用模型；有预约关联时先处理预约。移除保留历史，可恢复。')
    if st.session_state.get('catalog_pending'):
        operation,request=st.session_state['catalog_pending'];st.warning('上次器材请求的返回结果未确认，请先查询，避免重复保存。')
        if st.button('查询器材保存结果',key='catalog_query_pending'):
            saved=controller.service.find_equipment_command(request.idempotency_key)
            if saved:
                st.session_state.pop('catalog_pending',None);st.session_state['catalog_result']=saved;st.session_state.pop('catalog_preview',None);st.rerun()
            st.info('暂未找到保存结果，请先核对列表，不能据此认定失败。')
        return
    result=st.session_state.get('catalog_result')
    if result:
        current=next((row for row in entries if row.equipment.id==result.record.equipment.id),result.record)
        st.success(safe_text(result.message));card('最新器材读回',current.equipment,current.archived)
        st.caption('器材编号：'+current.equipment.id+' · 版本 '+str(current.equipment.version))
        if result.invalidated_proposal_ids:st.info(f'{len(result.invalidated_proposal_ids)}项待批准方案因资源变化已过期，请重新核对。')
        st.button('返回器材列表',key='catalog_done',on_click=open_catalog,args=(ACTIONS[0],));return
    preview=st.session_state.get('catalog_preview')
    if preview:
        operation,request=preview
        if operation in ('create','update'):
            existing=next((row.equipment for row in entries if row.equipment.id==getattr(request,'equipment_id',None)),None)
            card('请核对器材信息（尚未保存）',request,status=existing.status if existing else None)
        else:
            row=next((row for row in entries if row.equipment.id==request.equipment_id),None)
            if row:card('即将移除的器材' if operation=='archive' else '即将恢复的器材',row.equipment,row.archived)
            st.warning('移除只归档，不删除历史，也不改预约。' if operation=='archive' else '恢复保留移除前的设备状态；故障器材不会被当作修好。')
            st.write('原因：'+safe_text(request.reason))
        st.checkbox('我已核对器材信息并确认操作',key='catalog_confirm',on_change=activity)
        label={'create':'确认新增器材','update':'确认保存器材修改','archive':'确认移除器材','restore':'确认恢复器材'}[operation]
        if st.button(label,key='catalog_commit',type='primary',disabled=controller.read_only or not st.session_state.get('catalog_confirm')):
            st.session_state['catalog_result']=controller.dispatch(operation,request);st.session_state.pop('catalog_preview',None);st.rerun()
        if st.button('返回编辑',key='catalog_back'):
            clear_view();st.session_state['catalog_backup']=request;st.rerun()
        return
    rows=tuple(row for row in entries if row.archived==(action==ACTIONS[4]))
    if action==ACTIONS[0]:
        query=st.text_input('搜索台账',placeholder='名称、类别或编号',key='catalog_search',on_change=activity)
        found=tuple(row for row in rows if not query.strip() or query.strip().casefold() in (row.equipment.name+' '+row.equipment.category+' '+row.equipment.id).casefold())
        st.caption(f'在用台账 {len(rows)} 台；已移除 {sum(row.archived for row in entries)} 台。')
        from campusops.ui.app import _table
        _table([{'器材':row.equipment.name,'类别':CATEGORY_LABELS.get(row.equipment.category,row.equipment.category),
            '能力':capabilities_label(row.equipment.capabilities),'状态':EQUIPMENT_LABELS[row.equipment.status],'编号':row.equipment.id[-8:]} for row in found])
        st.button('去新增器材',key='catalog_add',on_click=open_catalog,args=(ACTIONS[1],),disabled=controller.read_only);return
    selected=None
    if action!=ACTIONS[1]:
        if not rows:st.info('没有已移除器材。' if action==ACTIONS[4] else '暂无器材，请先新增。');return
        ids=tuple(row.equipment.id for row in rows)
        saved=st.session_state.get('catalog_selected_id');saved=saved if saved in ids else ids[0]
        st.session_state['catalog_selected_id']=saved;st.session_state['catalog_selected']=saved
        chosen=st.selectbox('选择要管理的器材',ids,index=None,key='catalog_selected',on_change=select_record,
            format_func=lambda value:next(row.equipment.name+' · '+('已移除' if row.archived else EQUIPMENT_LABELS[row.equipment.status])+' · '+value[-8:] for row in rows if row.equipment.id==value))
        selected=next(row for row in rows if row.equipment.id==chosen);card('当前器材',selected.equipment,selected.archived)
    backup=st.session_state.get('catalog_backup')
    if action in (ACTIONS[1],ACTIONS[2]):
        change=selected is not None;initial=backup if isinstance(backup,EquipmentCreate) else selected.equipment if selected else None
        if change:
            revision=st.session_state.setdefault('catalog_revision',(selected.equipment.id,selected.equipment.version))
            if revision!=(selected.equipment.id,selected.equipment.version):
                st.warning('器材已被其他操作更新，当前表单未提交。请载入最新状态后重新填写。')
                st.button('载入最新器材信息',key='catalog_reload',on_click=clear_view);return
        categories=tuple(dict.fromkeys((*CATEGORY_LABELS,*(row.equipment.category for row in entries))))
        default=initial.category if initial else 'camera';epoch=st.session_state.get('catalog_epoch',0)
        with st.form('catalog_fields_'+str(epoch),enter_to_submit=False):
            name=st.text_input('器材名称',value=initial.name if initial else '',max_chars=100,key='catalog_name_'+str(epoch),placeholder='例如：社团相机 D')
            category=st.selectbox('器材类别',(*categories,'自定义类别'),index=categories.index(default) if default in categories else 0,
                format_func=lambda key:CATEGORY_LABELS.get(key,key),key='catalog_category_'+str(epoch))
            custom_category=st.text_input('自定义类别（选择自定义时填写）',max_chars=100,key='catalog_custom_category_'+str(epoch))
            caps=tuple(dict.fromkeys((*CAPABILITY_LABELS,*(cap for row in entries for cap in row.equipment.capabilities))))
            requirements=st.multiselect('器材具备的能力',caps,default=list(initial.capabilities) if initial else ['photo','video'],format_func=lambda cap:CAPABILITY_LABELS.get(cap,cap),key='catalog_caps_'+str(epoch))
            custom_caps=st.text_input('补充能力（可选，逗号分隔）',max_chars=300,key='catalog_extra_caps_'+str(epoch))
            actor=st.text_input('器材确认人匿名别名',value=backup.actor_alias if isinstance(backup,EquipmentCreate) else '管理员',max_chars=100,key='catalog_actor_'+str(epoch))
            note=st.text_area('器材说明（可选）',value=backup.note if isinstance(backup,EquipmentCreate) else '',max_chars=500,key='catalog_note_'+str(epoch))
            submitted=st.form_submit_button('预览器材修改' if change else '预览新增器材',key='catalog_preview_submit',type='primary',disabled=controller.read_only,on_click=activity)
        if submitted:
            values=dict(name=name.strip(),category=custom_category.strip() if category=='自定义类别' else category,
                capabilities=tuple(dict.fromkeys((*requirements,*(value.strip() for value in re.split('[,，、]',custom_caps) if value.strip())))),
                actor_alias=actor.strip(),note=note.strip(),idempotency_key='catalog_'+uuid4().hex)
            try:request=EquipmentUpdate(**values,equipment_id=selected.equipment.id,expected_equipment_version=selected.equipment.version) if change else EquipmentCreate(**values)
            except ValidationError:st.warning('请填写名称、类别、至少一项能力和匿名确认人。');return
            st.session_state['catalog_preview']=('update' if change else 'create',request);st.session_state.pop('catalog_confirm',None);st.rerun()
    else:
        operation='restore' if action==ACTIONS[4] else 'archive'
        associated=tuple(row for row in controller.service.list_reservations() if row.equipment_id==selected.equipment.id and row.status=='ACTIVE')
        if operation=='archive' and associated:
            st.warning(f'此器材仍有{len(associated)}条有效预约。请先到预约管理调整或取消，再移除。')
        with st.form('catalog_archive_form',enter_to_submit=False):
            actor=st.text_input('操作确认人匿名别名',value=backup.actor_alias if isinstance(backup,EquipmentArchiveCreate) else '管理员',max_chars=100,key='catalog_archive_actor')
            reason=st.text_area('操作原因',value=backup.reason if isinstance(backup,EquipmentArchiveCreate) else '',max_chars=500,key='catalog_archive_reason')
            submitted=st.form_submit_button('预览恢复器材' if operation=='restore' else '预览移除器材',key='catalog_archive_preview',disabled=controller.read_only or operation=='archive' and bool(associated),on_click=activity)
        if submitted:
            model=EquipmentRestoreCreate if operation=='restore' else EquipmentArchiveCreate
            try:request=model(equipment_id=selected.equipment.id,expected_equipment_version=selected.equipment.version,actor_alias=actor.strip(),reason=reason.strip(),idempotency_key='catalog_'+uuid4().hex)
            except ValidationError:st.warning('请填写确认人和操作原因。');return
            st.session_state['catalog_preview']=(operation,request);st.session_state.pop('catalog_confirm',None);st.rerun()
