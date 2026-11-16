import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const shares={list:()=>api('/shares'),get:id=>api('/shares/'+id),create:data=>api('/shares',{method:'POST',body:JSON.stringify(data)}),resume:id=>api('/shares/'+id+'/resume',{method:'POST'}),revoke:id=>api('/shares/'+id+'/revoke',{method:'POST'}),credential:id=>api('/shares/'+id+'/credential')};
