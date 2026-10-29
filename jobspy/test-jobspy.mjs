import {test} from 'node:test';
import assert from 'node:assert/strict';
import {decode,invocation,execute} from '../../providers/jobspy.mjs';
const entry={name:'JobSpy Indeed',jobspy:{site:'indeed',python:'/tmp/.venv/bin/python',checkout:'/tmp/JobSpy',queries:['software engineer'],location:'San Francisco, CA'}};
const data={schema:'career-ops-jobspy-v1',status:'ok',cacheReused:false,jobs:[{title:'Engineer',url:'https://example.com/job',company:'Example'}],runs:[]};
test('bounded explicit single-board invocation',()=>{const spec=invocation(entry);assert.equal(spec.timeout,137000);assert.ok(spec.args.includes('48'));assert.equal(spec.args.filter(x=>x==='--site').length,1);assert.throws(()=>invocation({...entry,jobspy:{...entry.jobspy,queries:Array(4).fill('x')}}));});
test('partial results survive with visible errors',()=>{const p={...data,status:'partial',runs:[{status:'error',query:'q',errors:{indeed:'429'}}]};const result=decode({code:3,stdout:JSON.stringify(p)},entry);assert.equal(result.jobs.length,1);assert.match(result.warnings[0],/429/);});
test('total failure and cached output rejected',()=>{assert.throws(()=>decode({code:3,stdout:JSON.stringify({...data,status:'error',jobs:[]})},entry));assert.throws(()=>decode({code:0,stdout:JSON.stringify({...data,cacheReused:true})},entry));});
test('worker timeout enforced',async()=>{await assert.rejects(execute({command:process.execPath,args:['-e','setInterval(()=>{},1000)'],cwd:'/tmp',timeout:50}),/timeout/);});
