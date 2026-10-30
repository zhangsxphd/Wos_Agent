import hashlib, io, json, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from scripts.pilot import pmc_supplements as subject

class TestOfficialSupplementInventory(unittest.TestCase):
 def test_reuses_matching_file_and_does_not_download_unlinked_figure(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td).resolve();oldfile=root/'saved'/'s001.zip';oldfile.parent.mkdir()
   stream=io.BytesIO()
   with zipfile.ZipFile(stream,'w') as z:z.writestr('table.txt','test')
   data=stream.getvalue();oldfile.write_bytes(data)
   checksum=hashlib.md5(data).hexdigest();pmc='PMC123';doi='10.1/example';key=pmc+'.1'
   listing=b'<ListBucketResult><CommonPrefixes><Prefix>PMC123.1/</Prefix></CommonPrefixes></ListBucketResult>'
   xml=b'<article xmlns:xlink="http://www.w3.org/1999/xlink"><supplementary-material xlink:href="s001.zip"/></article>'
   meta={'doi':doi,'is_manuscript':False,'is_retracted':False,'is_pmc_openaccess':True,'xml_url':'s3://pmc-oa-opendata/'+key+'/'+key+'.xml?md5='+hashlib.md5(xml).hexdigest(),'media_urls':['s3://pmc-oa-opendata/'+key+'/s001.zip?md5='+checksum,'s3://pmc-oa-opendata/'+key+'/figure.jpg?md5='+checksum]}
   def read(url,limit=None):
    if 'list-type=' in url:return listing
    if '/metadata/' in url:return json.dumps(meta).encode()
    if url.endswith('.xml'):return xml
    raise AssertionError('supplement or unrelated figure unexpectedly downloaded: '+url)
   old={(doi,'s001.zip'):{'local_file':'saved/s001.zip'}}
   with patch.object(subject,'ROOT',root),patch.object(subject,'BASE',root/'out'),patch.object(subject,'read_url',side_effect=read):
    result=subject.process({'doi':doi},{doi:{'pmcid':pmc,'doi':doi}},old)
   self.assertEqual(result['status'],'checked');self.assertEqual(len(result['files']),1)
   self.assertEqual(result['files'][0]['status'],'reused_previously_downloaded',result)
   self.assertEqual(result['files'][0]['sha256'],hashlib.sha256(data).hexdigest())
   self.assertEqual(oldfile.read_bytes(),data)
 def test_rejects_doi_mismatch_before_network(self):
  with patch.object(subject,'read_url',side_effect=AssertionError('network must not be used')):
   result=subject.process({'doi':'10.1/expected'},{'10.1/expected':{'pmcid':'PMC123','doi':'10.1/other'}},{})
  self.assertEqual(result['status'],'id_converter_mismatch')

if __name__=='__main__':unittest.main()
