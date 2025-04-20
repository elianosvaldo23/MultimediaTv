import dropbox
import os
import time
import logging

# Configurar logging
logger = logging.getLogger(__name__)

class DropboxStorage:
    def __init__(self, token, local_file):
        """Inicializa el almacenamiento de Dropbox
        
        Args:
            token (str): Token de acceso de Dropbox
            local_file (str): Ruta al archivo local de base de datos
        """
        self.dbx = dropbox.Dropbox(token)
        self.local_file = local_file
        self.remote_path = f"/{os.path.basename(self.local_file)}"
        self.last_sync_time = 0
        
        # Intentar descargar la base de datos existente
        self.download_if_exists()
    
    def download_if_exists(self):
        """Descarga la base de datos desde Dropbox si existe"""
        try:
            # Verificar si el archivo existe en Dropbox
            self.dbx.files_get_metadata(self.remote_path)
            
            # Descargar el archivo
            with open(self.local_file, "wb") as f:
                metadata, res = self.dbx.files_download(self.remote_path)
                f.write(res.content)
                
            logger.info(f"Base de datos descargada desde Dropbox: {self.local_file}")
            print(f"Base de datos descargada desde Dropbox: {self.local_file}")
        except dropbox.exceptions.ApiError as e:
            if isinstance(e.error, dropbox.files.GetMetadataError) and e.error.is_path():
                logger.info(f"El archivo no existe en Dropbox, se creará una base de datos nueva")
                print(f"El archivo no existe en Dropbox, se creará una base de datos nueva")
            else:
                logger.error(f"Error de Dropbox: {e}")
                print(f"Error de Dropbox: {e}")
    
    def upload(self, force=False):
        """Sube la base de datos a Dropbox
        
        Args:
            force (bool): Si es True, fuerza la sincronización incluso si el archivo
                         no ha cambiado recientemente
        """
        current_time = time.time()
        
        # Verificar si ha pasado suficiente tiempo desde la última sincronización
        if not force and current_time - self.last_sync_time < 60:  # Mínimo 60 segundos entre sincronizaciones
            return
        
        # Verificar si el archivo existe y ha sido modificado
        if not os.path.exists(self.local_file):
            logger.warning(f"El archivo local no existe: {self.local_file}")
            return
        
        try:
            # Subir archivo a Dropbox
            file_size = os.path.getsize(self.local_file)
            
            # Para archivos pequeños, subir en una sola operación
            if file_size <= 4 * 1024 * 1024:  # 4 MB
                with open(self.local_file, "rb") as f:
                    self.dbx.files_upload(
                        f.read(), 
                        self.remote_path, 
                        mode=dropbox.files.WriteMode.overwrite
                    )
            else:
                # Para archivos grandes, usar upload_session
                chunk_size = 4 * 1024 * 1024  # 4 MB por chunk
                
                with open(self.local_file, "rb") as f:
                    upload_session_start_result = self.dbx.files_upload_session_start(
                        f.read(chunk_size)
                    )
                    cursor = dropbox.files.UploadSessionCursor(
                        session_id=upload_session_start_result.session_id,
                        offset=f.tell()
                    )
                    commit = dropbox.files.CommitInfo(
                        path=self.remote_path,
                        mode=dropbox.files.WriteMode.overwrite
                    )
                    
                    while f.tell() < file_size:
                        if (file_size - f.tell()) <= chunk_size:
                            self.dbx.files_upload_session_finish(
                                f.read(chunk_size),
                                cursor,
                                commit
                            )
                        else:
                            self.dbx.files_upload_session_append_v2(
                                f.read(chunk_size),
                                cursor
                            )
                            cursor.offset = f.tell()
            
            self.last_sync_time = current_time
            logger.info(f"Base de datos sincronizada con Dropbox: {self.local_file}")
            print(f"Base de datos sincronizada con Dropbox: {self.local_file}")
        except Exception as e:
            logger.error(f"Error sincronizando con Dropbox: {e}")
            print(f"Error sincronizando con Dropbox: {e}")
    
    def force_sync(self):
        """Fuerza una sincronización inmediata con Dropbox"""
        self.upload(force=True)
