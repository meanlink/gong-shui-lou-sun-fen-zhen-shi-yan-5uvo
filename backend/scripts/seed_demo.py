import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, SharePackage
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.services import shares as share_service
from src.utils.security import hash_password
from src.utils.serialization import checksum
def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
            db.commit()
        if not db.scalar(select(SharePackage).where(SharePackage.name=='巡检复核共享包 · 示例')):
            admin=db.scalar(select(User).where(User.role=='admin'))
            job=db.scalar(select(Job).where(Job.approved==True).order_by(Job.id))
            if admin and job:
                c=share_service.new_credentials()
                package=SharePackage(public_id=c['public_id'],name='巡检复核共享包 · 示例',description='供合作方核对留出集分诊结果；原始管号已替换为包内代号。',
                    job_id=job.id,created_by=admin.id,salt=c['salt'],token_hash=c['token_hash'],token_hint=c['token_hint'],
                    token_cipher=c['token_cipher'],status='building',expires_at=time.time()+7*86400,created_at=time.time())
                db.add(package);db.commit()
                share_service.build(package.id)
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,SharePackage]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
