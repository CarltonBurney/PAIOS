"""Contract preflight only; does not implement or test runtime adapters."""
from pathlib import Path
import ast, copy, hashlib, json, sys, types
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
ROOT=Path(__file__).resolve().parents[1]
SCHEMA=json.loads((ROOT/'schemas/contracts.schema.json').read_text(encoding='utf-8'))
checks=[]
def check(name,fn): fn(); checks.append(name)
def shape(kind,v):
    Draft202012Validator({'$ref':'#/$defs/'+kind,'$defs':SCHEMA['$defs']},format_checker=FormatChecker()).validate(v)
def digest(v):
    return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def ocr_semantics(o):
    s=o['status']; pages=o['pages']; nums=[p['page_number'] for p in pages]
    assert nums==sorted(set(nums)), 'OCR pages must be unique and ordered'
    good=[p for p in pages if p['status']=='completed']; bad=[p for p in pages if p['status']=='failed']
    for p in good: assert p['text'] is not None and p['error'] is None
    for p in bad: assert p['text'] is None and p['blocks']==[] and p['error'] is not None
    if s=='completed': assert pages and not bad and o['error'] is None
    if s=='partial': assert good and bad
    if s in ('completed','partial'): assert o['full_text']=='\f'.join(p['text'] for p in good)
    if s=='failed': assert not good and o['full_text'] is None and o['error'] is not None
    if s in ('skipped','reused'): assert not pages and o['full_text'] is None and o['error'] is None
    assert (o['reused_from_result_id'] is not None)==(s=='reused')
def bundle_semantics(b):
    r,a,i=b['registry'],b['audit'],b['index']
    assert b['scope']==r['scope']==a['scope']==i['scope']
    assert r['asset_id']==a['asset_id']==i['asset_id'] and r['ingestion_id']==a['ingestion_id']
    assert r['canonical_record_version']==a['registry_version']==i['registry_version']
    assert a['previous_registry_version']==a['registry_version']-1
    assert (a['previous_event_id'] is None)==(a['registry_version']==1)
    assert r['last_event_id']==a['event_id']==i['event_id']
    assert r['status']==a['outcome']==i['status']
    assert r['index_metadata']==a['index_metadata']==i['index_metadata']
    assert r['source']==a['source'] and r['error']==a['error'] and r['original']==i['original']
    assert digest(r)==a['registry_sha256'] and digest(i)==a['index_sha256']
    assert r['created_at']<=r['updated_at']
    for v in (r['created_at'],r['updated_at'],a['timestamp'],i['updated_at']): assert v.endswith('Z')
    if r['status']=='failed': assert r['error'] is not None
    if r['status']=='completed': assert r['original'] and r['identity']['sha256']
    if r['normalized']:
        n=r['normalized']; pages=n['pages']
        assert n['asset_id']==r['asset_id'] and n['scope']==r['scope']
        assert n['original_sha256']==r['identity']['sha256']==r['original']['sha256']
        assert [p['page_number'] for p in pages]==list(range(1,len(pages)+1))
        assert r['index_metadata']['page_count']==len(pages)
        for p in pages: assert p['sha256']==p['storage_ref']['sha256']
    if r['ocr']:
        o=r['ocr']; ocr_semantics(o)
        assert o['asset_id']==r['asset_id'] and o['scope']==r['scope']
        assert r['index_metadata']['ocr_status']==o['status']
        if o['status'] in ('completed','partial'):
            assert [p['page_number'] for p in o['pages']]==[p['page_number'] for p in r['normalized']['pages']]
            assert i['search_text']==o['full_text']
        if o['status']=='partial': assert r['status']=='partial'
    if r['index_metadata']['text_sha256'] is not None:
        assert r['index_metadata']['text_sha256']==hashlib.sha256(i['search_text'].encode()).hexdigest()
def reject(name,fn):
    try: fn()
    except (AssertionError,ValidationError): checks.append(name); return
    raise AssertionError('Invalid mutation accepted: '+name)
def resolve_refs(path,v):
    if isinstance(v,dict):
        if '$ref' in v:
            target,_,frag=v['$ref'].partition('#')
            doc=json.loads((path.parent/target if target else path).read_text(encoding='utf-8'))
            for p in frag.lstrip('/').split('/') if frag else []: doc=doc[p.replace('~1','/').replace('~0','~')]
        for item in v.values(): resolve_refs(path,item)
    elif isinstance(v,list):
        for item in v: resolve_refs(path,item)
def main():
    for path in sorted((ROOT/'schemas').glob('*.json')):
        v=json.loads(path.read_text(encoding='utf-8'))
        check('schema '+path.name,lambda v=v: Draft202012Validator.check_schema(v))
        check('refs '+path.name,lambda p=path,v=v: resolve_refs(p,v))
    for name,d in SCHEMA['$defs'].items(): check('definition '+name,lambda d=d: Draft202012Validator.check_schema(d))
    mapping={'accepted-bundle':'CommitBundle','processing-bundle':'CommitBundle','completed-bundle':'CommitBundle','failed-bundle':'CommitBundle','normalized-media':'NormalizedMedia','ocr-result':'OCRResult','error':'Error','ingest-request':'IngestRequest','ocr-partial':'OCRResult','ocr-failed':'OCRResult','ocr-skipped':'OCRResult','ocr-reused':'OCRResult'}
    mapping.update({'ingest-accepted':'IngestAccepted','job':'Job','search-result':'SearchResult'})
    fixtures={}
    for name,kind in mapping.items():
        v=json.loads((ROOT/'examples'/f'{name}.json').read_text(encoding='utf-8')); fixtures[name]=v
        check('fixture '+name,lambda k=kind,v=v: shape(k,v))
        if kind=='CommitBundle': check('invariants '+name,lambda v=v: bundle_semantics(v))
        if kind=='OCRResult': check('OCR semantics '+name,lambda v=v: ocr_semantics(v))
    for prev,nxt in [('accepted-bundle','processing-bundle'),('processing-bundle','completed-bundle'),('processing-bundle','failed-bundle')]:
        assert fixtures[nxt]['audit']['previous_event_id']==fixtures[prev]['audit']['event_id']; checks.append('lineage '+prev+' to '+nxt)
    bad=copy.deepcopy(fixtures['ingest-request']); bad['unrecognized']=True
    reject('reject unknown field',lambda: shape('IngestRequest',bad))
    bad=copy.deepcopy(fixtures['normalized-media']); bad['pages'][0]['width']=0
    reject('reject zero width',lambda: shape('NormalizedMedia',bad))
    bad=copy.deepcopy(fixtures['ocr-result']); bad['result_id']='not-a-uuid'
    reject('reject invalid UUID',lambda: shape('OCRResult',bad))
    bad=copy.deepcopy(fixtures['ocr-skipped']); bad['full_text']='invented'
    reject('reject skipped OCR with fabricated text',lambda: shape('OCRResult',bad))
    bad=copy.deepcopy(fixtures['ocr-result']); bad['pages'][0]['status']='failed'
    reject('reject failed OCR page with success text',lambda: shape('OCRResult',bad))
    bad=copy.deepcopy(fixtures['ocr-result']); bad['full_text']='wrong'
    reject('reject inconsistent OCR text',lambda: ocr_semantics(bad))
    for field,v in [('registry_version',99),('scope',{'tenant_id':'other','workspace_id':'example-workspace'}),('search_text','tampered')]:
        bad=copy.deepcopy(fixtures['completed-bundle']); bad['index'][field]=v
        reject('reject index '+field,lambda: bundle_semantics(bad))
    bad=copy.deepcopy(fixtures['completed-bundle']); bad['audit']['index_metadata']['title']='diverged'
    reject('reject divergent metadata',lambda: bundle_semantics(bad))
    bad=copy.deepcopy(fixtures['completed-bundle']); bad['registry']['normalized']['pages'][0]['page_number']=2
    bad['audit']['registry_sha256']=digest(bad['registry'])
    reject('reject noncontiguous pages',lambda: bundle_semantics(bad))
    path=ROOT/'api/openapi.json'; api=json.loads(path.read_text(encoding='utf-8'))
    check('OpenAPI reference integrity',lambda: resolve_refs(path,api))
    assert api['openapi']=='3.1.0' and len(api['paths'])==4; checks.append('OpenAPI expected operations')
    source=(ROOT/'interfaces/contracts.py').read_text(encoding='utf-8')
    check('protocol syntax',lambda: ast.parse(source))
    module=types.ModuleType('contract_stub_check'); sys.modules[module.__name__]=module
    check('protocol import',lambda: exec(compile(source,'contracts.py','exec'),module.__dict__))
    report={'status':'passed','checks_passed':len(checks),'checks':checks,'scope':'JSON Schema meta-validation, fixtures, selected invariants, negative mutations, reference integrity and protocol syntax/import. Not full OpenAPI specification validation or runtime integration.','runtime_tests':'not run; Claude implementations not included'}
    (ROOT/'validation-report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'passed','checks_passed':len(checks)}))
if __name__=='__main__': main()
