"""The actual professional photo routes, isolated storage and synthetic images."""
import base64
import io
import os
import random
import struct
import tempfile
import unittest
import zlib
from unittest.mock import patch
from PIL import Image
from app.core import permissions, photo_guard
from app.patient_web import doctor_photos


def encoded(raw):
    return {'data_b64': base64.b64encode(raw).decode(), 'filename': 'ignored.exe', 'mime': 'text/plain'}


def picture(fmt='JPEG', size=(120, 200), color=(180, 130, 80), orientation=None):
    out = io.BytesIO()
    exif = Image.Exif()
    if orientation: exif[274] = orientation
    Image.new('RGB', size, color).save(out, format=fmt, exif=exif)
    return out.getvalue()


class ProfessionalPhotoUpload(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from app.patient_web.doctor_photos_router import router
        app = FastAPI(); app.include_router(router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        for target, value in [
            ('app.core.app_modules.is_enabled', lambda module_id: module_id=='turnos'),
            ('app.auth.admin_auth._verify_token', lambda token: {'usuario': token, 'rol': token} if token in ('medico','admin') else None),
            ('app.auth.admin_auth.account_security', lambda *a, **k: {'tiene_2fa':True, '2fa_deadline':None, '2fa_vencido':False}),
            ('app.core.permissions.access_for', lambda user, role: {'slug':role, 'state':'ok', 'permissions':frozenset(permissions.BUILTIN_ROLES[role]['permissions'])}),
            ('app.core.db_operators.operator_doctor_id', lambda user:'doctor'),
            ('app.patient_web.doctor_photos._doc', lambda *a:{'id':'doctor', 'nombre':'Professional fixture'}),
            ('app.patient_web.doctor_photos._invalidar_sprite', lambda:None),
        ]:
            p=patch(target, side_effect=value); p.start(); self.addCleanup(p.stop)
        p=patch.object(doctor_photos,'PHOTOS_DIR',self.temp.name); p.start(); self.addCleanup(p.stop)
        self.client.cookies.set('st_admin','medico')
        self.url='/api/admin/doctors/doctor/photo'

    def upload(self, raw):
        return self.client.post(self.url,json=encoded(raw))

    def assert_published(self, raw):
        response=self.upload(raw)
        self.assertEqual(response.status_code,200,response.text)
        url=response.json()['foto_url']
        self.assertEqual(doctor_photos.original_bytes('doctor'),raw)
        public=self.client.get(url)
        self.assertEqual(public.status_code,200)
        self.assertEqual(public.headers['content-type'],'image/webp')
        with Image.open(io.BytesIO(public.content)) as image:
            self.assertEqual(image.size,(512,512)); image.load()
        preview=self.client.get(self.url)
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(preview.json()['foto_url'],url)
        return url,public.content

    def test_jpeg_png_webp_replace_and_reload_agree_with_public_photo(self):
        urls=[]
        for fmt,color in [('JPEG',(220,20,30)),('PNG',(30,220,20)),('WEBP',(20,30,220))]:
            urls.append(self.assert_published(picture(fmt,color=color))[0])
        self.assertEqual(len(set(urls)),3)

    def test_heic_real_codec_upload_original_preview_and_rotate(self):
        self.assertTrue(photo_guard.heif_available(),'HEIC codec must be installed for release validation')
        import pillow_heif
        pillow_heif.register_heif_opener()
        raw=picture('HEIF')
        from app.patient_web.portrait_processing import oriented
        self.assertEqual(oriented(raw).size,(120,200))
        self.assert_published(raw)
        preview=self.client.get(self.url+'?original=1')
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertEqual(preview.headers['content-type'],'image/jpeg')
        with Image.open(io.BytesIO(preview.content)) as image:self.assertEqual(image.size,(120,200))
        rotated=self.client.post(self.url,json={'action':'rotate','rotation':90})
        self.assertEqual(rotated.status_code,200,rotated.text)
        self.assertEqual(rotated.json()['rotation'],90)
        self.assertEqual(doctor_photos.original_bytes('doctor'),raw)

    def test_ordinary_photo_larger_than_old_five_mb_limit(self):
        image=Image.frombytes('RGB',(1500,1500),random.Random(7).randbytes(1500*1500*3))
        out=io.BytesIO();image.save(out,format='PNG');raw=out.getvalue()
        self.assertGreater(len(raw),5*1024*1024)
        self.assert_published(raw)

    def test_large_phone_photo_is_resized_and_exif_orientation_is_applied(self):
        raw=picture(size=(7000,4000),orientation=6)
        from app.patient_web.portrait_processing import oriented
        self.assertEqual(oriented(raw).size,(4000,7000))
        self.assert_published(raw)

    def test_invalid_bytes_and_oversize_leave_previous_original_and_public_intact(self):
        raw=picture();url,public=self.assert_published(raw)
        for invalid in (b'<svg><script>alert(1)</script></svg>', b'\xff\xd8\xffbroken', b'x'*(20*1024*1024+1)):
            r=self.upload(invalid);self.assertIn(r.status_code,(400,413),r.text)
            self.assertEqual(doctor_photos.original_bytes('doctor'),raw)
            self.assertEqual(self.client.get(url).content,public)
        r=self.client.post(self.url,json={'data_b64':'!!!!'})
        self.assertEqual(r.status_code,400)

    def test_professional_pixel_cap_rejects_before_decode_and_retains_previous(self):
        raw=picture();url,public=self.assert_published(raw)
        png=picture('PNG')
        ihdr=struct.pack('>II',6000,9000)+png[24:29]  # 54 MP, bounded header only
        too_large=png[:16]+ihdr+struct.pack('>I',zlib.crc32(b'IHDR'+ihdr)&0xffffffff)+png[33:]
        response=self.upload(too_large)
        self.assertEqual(response.status_code,400,response.text)
        self.assertIn('50 megapíxeles',response.json()['error'])
        self.assertEqual(doctor_photos.original_bytes('doctor'),raw)
        self.assertEqual(self.client.get(url).content,public)

    def test_missing_codec_has_clear_result_and_no_partial_replacement(self):
        import pillow_heif
        pillow_heif.register_heif_opener();raw=picture('HEIF')
        before=picture();self.assert_published(before)
        with patch.object(photo_guard,'heif_available',return_value=False):
            r=self.upload(raw)
        self.assertEqual(r.status_code,400,r.text)
        self.assertIn('HEIC',r.json()['error'])
        self.assertEqual(doctor_photos.original_bytes('doctor'),before)

    def test_failed_codec_registration_has_clear_upload_and_original_preview_errors(self):
        import pillow_heif
        pillow_heif.register_heif_opener(); raw=picture('HEIF')
        url,public=self.assert_published(raw)
        for unavailable in (False, True):
            with self.subTest(missing_decoder=unavailable), \
                 patch.object(photo_guard,'_HEIF_OPENED',False), \
                 patch.object(photo_guard,'heif_available',return_value=not unavailable), \
                 patch.object(pillow_heif,'register_heif_opener',side_effect=RuntimeError('codec unavailable')):
                for response in (self.upload(raw), self.client.get(self.url+'?original=1')):
                    self.assertEqual(response.status_code,400,response.text)
                    self.assertIn('JPG',response.json()['error'])
            self.assertEqual(doctor_photos.original_bytes('doctor'),raw)
            self.assertEqual(self.client.get(url).content,public)

    def test_heif_codec_orientation_is_not_applied_twice_or_changed_in_original(self):
        import pillow_heif
        pillow_heif.register_heif_opener();raw=picture('HEIF',orientation=6)
        from app.patient_web.portrait_processing import oriented
        # pillow-heif normalizes orientation through the HEIF container and
        # marks EXIF upright. Applying original_orientation again would rotate
        # a second time. This fixture's decoded dimensions remain 120 x 200.
        with Image.open(io.BytesIO(raw)) as image:
            self.assertEqual(image.info['original_orientation'],6)
            self.assertEqual(image.getexif()[274],1)
        self.assertEqual(oriented(raw).size,(120,200))
        self.assert_published(raw)

    def test_foreign_doctor_and_unauthenticated_upload_still_refused(self):
        r=self.client.post('/api/admin/doctors/foreign/photo',json=encoded(picture()))
        self.assertIn(r.status_code,(403,404))
        r=self.client.post('/api/admin/centers/fixture/photo',json=encoded(picture()))
        self.assertIn(r.status_code,(403,404))
        self.client.cookies.clear()
        self.assertEqual(self.upload(picture()).status_code,401)

    def test_center_uses_same_real_decoder_and_bad_upload_keeps_previous(self):
        self.client.cookies.set('st_admin','admin')
        with tempfile.TemporaryDirectory() as folder, patch.object(doctor_photos,'CENTER_PHOTOS_DIR',folder):
            import pillow_heif
            pillow_heif.register_heif_opener()
            for fmt in ('JPEG','PNG','WEBP','HEIF'):
                r=self.client.post('/api/admin/centers/fixture/photo',json=encoded(picture(fmt)))
                self.assertEqual(r.status_code,200,r.text)
                url=r.json()['foto_url']; photo=self.client.get(url)
                self.assertEqual(photo.status_code,200)
                with Image.open(io.BytesIO(photo.content)) as image:
                    self.assertEqual(image.format,'JPEG');image.load()
                preview=self.client.get('/api/admin/centers/fixture/photo')
                self.assertEqual(preview.json()['foto_url'],url)
            r=self.client.post('/api/admin/centers/fixture/photo',json=encoded(b'\xff\xd8\xffbad'))
            self.assertEqual(r.status_code,400)
            self.assertEqual(self.client.get(url).content,photo.content)

    def test_center_atomic_replace_failure_keeps_previous_photo_and_retry_commits(self):
        self.client.cookies.set('st_admin','admin')
        for extension,fmt in (('png','PNG'),('jpg','JPEG')):
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as folder, \
                 patch.object(doctor_photos,'CENTER_PHOTOS_DIR',folder):
                old=picture(fmt,color=(10,20,30))
                path=os.path.join(folder,'fixture.'+extension)
                with open(path,'wb') as dest:dest.write(old)
                url=doctor_photos.photo_url('fixture','centers')
                with patch.object(doctor_photos.os,'replace',side_effect=OSError('synthetic write failure')):
                    response=self.client.post('/api/admin/centers/fixture/photo',json=encoded(picture('PNG')))
                self.assertEqual(response.status_code,507,response.text)
                self.assertEqual(doctor_photos.photo_url('fixture','centers'),url)
                self.assertEqual(self.client.get(url).content,old)
                self.assertEqual(os.listdir(folder),['fixture.'+extension])
                response=self.client.post('/api/admin/centers/fixture/photo',json=encoded(picture('PNG')))
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(os.listdir(folder),['fixture.jpg'])
                current=self.client.get(response.json()['foto_url'])
                self.assertNotEqual(current.content,old)
                self.assertEqual(current.headers['content-type'],'image/jpeg')

    def test_route_rejects_oversize_json_before_parsing_for_each_upload_surface(self):
        # Test the streaming reader with small limits; production limits remain
        # 20 MB for professionals, 5 MB for centers, plus base64/JSON overhead.
        with patch.object(doctor_photos,'MAX_BYTES',1024):
            for url in (self.url,'/api/mi/foto'):
                response=self.client.post(url,content=b' '*(66*1024),headers={'Content-Type':'application/json'})
                self.assertEqual(response.status_code,413,response.text)
        self.client.cookies.set('st_admin','admin')
        response=self.client.post('/api/admin/centers/fixture/photo',content=b' '*(7*1024*1024),headers={'Content-Type':'application/json'})
        self.assertEqual(response.status_code,413,response.text)

    def test_bounded_reader_stops_consuming_chunked_body_after_limit(self):
        import asyncio
        from app.patient_web.doctor_photos_router import _read_json_lenient_or_400
        class ChunkedRequest:
            consumed=0
            async def stream(self):
                for _ in range(20):
                    self.consumed+=1
                    yield b' '*(32*1024)
        request=ChunkedRequest()
        with patch.object(doctor_photos,'MAX_BYTES',1024):
            result,error=asyncio.run(_read_json_lenient_or_400(request))
        self.assertIsNone(result)
        self.assertEqual(error.status_code,413)
        self.assertEqual(request.consumed,3)
