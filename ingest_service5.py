import boto3
import pandas as pd
import json
import os
import logging
from botocore.config import Config
from botocore.exceptions import BotoCoreError, NoCredentialsError, ClientError
from dotenv import load_dotenv
import time

# Configurar el logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d %(levelname)s %(name)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(f"/logs/{os.getenv('CONTAINER_NAME')}.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Cargar las variables de entorno desde el archivo .env
load_dotenv()

def create_boto3_session():
    """Crea una sesión de boto3 usando las credenciales especificadas en el archivo de configuración."""
    try:
        session = boto3.Session(region_name=os.getenv('AWS_REGION', 'us-east-1'))
        return session
    except (BotoCoreError, NoCredentialsError) as e:
        logger.error(f"Error al crear la sesión de boto3: {e}")
        raise

def scan_dynamodb_table(session, table_name):
    """Realiza un scan de una tabla DynamoDB con paginación."""
    dynamodb = session.client('dynamodb')
    paginator = dynamodb.get_paginator('scan')
    response_iterator = paginator.paginate(TableName=table_name)
    
    items = []
    for page in response_iterator:
        items.extend(page['Items'])
    
    return items

def transform_items(items):
    """Transforma los elementos de DynamoDB a un formato plano adecuado para CSV."""
    transformed_items = []

    for item in items:
        transformed_item = {}
        for key, value in item.items():
            # DynamoDB devuelve los valores como un diccionario con un solo par clave-valor
            if isinstance(value, dict):
                # Extraer el primer (y único) valor del diccionario
                data_type, data_value = next(iter(value.items()))
                if data_type == 'S':  # String
                    transformed_item[key] = data_value
                elif data_type == 'N':  # Number
                    transformed_item[key] = float(data_value) if '.' in data_value else int(data_value)
                elif data_type == 'BOOL':  # Boolean
                    transformed_item[key] = data_value
                elif data_type == 'M':  # Map (anidado)
                    # Aplanar el diccionario anidado
                    for sub_key, sub_value in data_value.items():
                        # Asumimos que los sub_valores son del mismo formato {tipo: valor}
                        sub_type, sub_val = next(iter(sub_value.items()))
                        transformed_item[f"{key}_{sub_key}"] = (
                            float(sub_val) if sub_type == 'N' and '.' in sub_val else
                            int(sub_val) if sub_type == 'N' else
                            sub_val
                        )
                elif data_type == 'L':  # Lista
                    # Convertir la lista en una cadena JSON para que sea legible
                    transformed_item[key] = json.dumps(data_value)
                else:
                    # Manejar tipos no comunes convirtiéndolos a cadena
                    transformed_item[key] = str(data_value)
            else:
                transformed_item[key] = value
        transformed_items.append(transformed_item)
    
    return transformed_items

def save_to_s3(session, data, bucket_name, file_name):
    """Guarda los datos en un bucket S3."""
    s3 = session.client('s3')
    s3.put_object(Bucket=bucket_name, Key=file_name, Body=data)

def create_glue_crawler(session, crawler_name, s3_target, role, database_name):
    """Crea un crawler de AWS Glue."""
    glue = session.client('glue')
    try:
        glue.create_crawler(
            Name=crawler_name,
            Role=role,
            DatabaseName=database_name,
            Targets={'S3Targets': [{'Path': s3_target}]},
            SchemaChangePolicy={
                'UpdateBehavior': 'UPDATE_IN_DATABASE',
                'DeleteBehavior': 'DEPRECATE_IN_DATABASE'
            }
        )
        logger.info(f"Crawler {crawler_name} creado exitosamente.")
    except glue.exceptions.AlreadyExistsException:
        logger.warning(f"Crawler {crawler_name} ya existe.")

def start_glue_crawler(session, crawler_name):
    """Inicia un crawler de AWS Glue."""
    glue = session.client('glue')
    try:
        glue.start_crawler(Name=crawler_name)
        logger.info(f"Crawler {crawler_name} iniciado.")
    except glue.exceptions.CrawlerRunningException:
        logger.warning(f"Crawler {crawler_name} ya está en ejecución.")
    except glue.exceptions.CrawlerNotFoundException:
        logger.error(f"Crawler {crawler_name} no encontrado.")
    except Exception as e:
        logger.error(f"Error al iniciar el crawler {crawler_name}: {e}")

def wait_for_crawler(glue_client, crawler_name, retries=20, delay=60):
    """Espera a que el crawler de AWS Glue complete su ejecución."""
    for _ in range(retries):
        try:
            response = glue_client.get_crawler(Name=crawler_name)
            state = response['Crawler']['State']
            logger.info(f"Estado del crawler {crawler_name}: {state}")
            if state == 'READY':
                return True
        except Exception as e:
            logger.error(f"Error al obtener el estado del crawler {crawler_name}: {e}")
        time.sleep(delay)
    raise Exception(f"El crawler {crawler_name} no completó su ejecución después de varios intentos.")

def main():
    # Variables de entorno para la configuración
    table_name = os.getenv('DYNAMODB_TABLE_5_PROD')
    bucket_name = os.getenv('S3_BUCKET_PROD')
    file_format = os.getenv('FILE_FORMAT', 'csv')
    role = os.getenv('AWS_ROLE_ARN')
    ingest_type = 'ingest-service-5'
    glue_database = f"glue_database_{ingest_type}_{table_name}_prod"
    glue_crawler_name = f"crawler_{ingest_type}_{table_name}_prod"
    
    if not table_name or not bucket_name:
        logger.error("Error: DYNAMODB_TABLE_5_PROD y S3_BUCKET_PROD son obligatorios.")
        return

    logger.info("Iniciando sesión de boto3...")
    session = create_boto3_session()
    
    try:
        logger.info(f"Escaneando la tabla DynamoDB: {table_name}...")
        items = scan_dynamodb_table(session, table_name)
    except ClientError as e:
        if e.response['Error']['Code'] == 'ExpiredTokenException':
            logger.error("El token de seguridad ha expirado. Por favor, renueva las credenciales de AWS.")
            return
        else:
            logger.error(f"Error al escanear la tabla DynamoDB: {e}")
            return
    
    logger.info("Transformando los elementos de DynamoDB...")
    transformed_items = transform_items(items)
    
    if file_format == 'csv':
        df = pd.DataFrame(transformed_items)
        data = df.to_csv(index=False)
        file_name = f'{ingest_type}/{table_name}.csv'  # Guardar en una carpeta específica
    else:
        data = json.dumps(transformed_items, indent=4)
        file_name = f'{ingest_type}/{table_name}.json'  # Guardar en una carpeta específica
    
    logger.info(f"Guardando datos en el bucket S3: {bucket_name}...")
    save_to_s3(session, data, bucket_name, file_name)
    
    logger.info(f"Ingesta de datos completada. Archivo subido a S3: {file_name}")
    logger.info(f"Ruta completa del archivo CSV: s3://{bucket_name}/{file_name}")
    
    # Crear y ejecutar el crawler de AWS Glue
    s3_target = f"s3://{bucket_name}/{ingest_type}/"  # Apuntar a la carpeta específica
    create_glue_crawler(session, glue_crawler_name, s3_target, role, glue_database)
    start_glue_crawler(session, glue_crawler_name)
    
    # Esperar a que el crawler complete su ejecución
    glue_client = session.client('glue')
    wait_for_crawler(glue_client, glue_crawler_name)

    # Eliminar la tabla existente para forzar la reconstrucción del esquema
    try:
        glue_client.delete_table(DatabaseName=glue_database, Name=f"{ingest_type}_{table_name}_csv")
        logger.info(f"Tabla {ingest_type}_{table_name}_csv eliminada para forzar la reconstrucción del esquema.")
    except glue_client.exceptions.EntityNotFoundException:
        logger.info(f"La tabla {ingest_type}_{table_name}_csv no existe, no es necesario eliminarla.")

if __name__ == "__main__":
    main()