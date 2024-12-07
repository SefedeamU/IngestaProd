import boto3
import pandas as pd
import json
import os
import logging
from botocore.exceptions import ClientError, NoCredentialsError
from dotenv import load_dotenv
import mysql.connector

# Configurar el logging
log_directory = "/home/ubuntu/logs"
if not os.path.exists(log_directory):
    os.makedirs(log_directory)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d %(levelname)s %(name)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(f"{log_directory}/etl_service.log"),
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
    except (ClientError, NoCredentialsError) as e:
        logger.error(f"Error al crear la sesión de boto3: {e}")
        raise

def save_to_mysql(df, table_name):
    """Guarda un DataFrame en una tabla MySQL."""
    try:
        conn = mysql.connector.connect(
            host=os.getenv('MYSQL_HOST'),
            user=os.getenv('MYSQL_USER'),
            password=os.getenv('MYSQL_PASSWORD'),
            database=os.getenv('MYSQL_DATABASE')
        )
        cursor = conn.cursor()
        
        # Crear la tabla si no existe
        columns = ', '.join([f'`{col}` TEXT' for col in df.columns])
        create_table_query = f'CREATE TABLE IF NOT EXISTS `{table_name}` ({columns})'
        logger.info(f"Creando tabla con la consulta: {create_table_query}")
        cursor.execute(create_table_query)
        
        # Insertar los datos
        for _, row in df.iterrows():
            values = ', '.join([f"'{val}'" for val in row])
            insert_query = f'INSERT INTO `{table_name}` VALUES ({values})'
            logger.info(f"Insertando datos con la consulta: {insert_query}")
            cursor.execute(insert_query)
        
        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"Datos guardados en MySQL, tabla: {table_name}.")
    except mysql.connector.Error as err:
        logger.error(f"Error al guardar datos en MySQL: {err}")

def main():
    logger.info("Iniciando sesión de boto3...")
    session = create_boto3_session()
    glue_client = session.client('glue')
    s3_bucket = os.getenv('S3_BUCKET_PROD')
    output_location = f"s3://{s3_bucket}/athena-results/"
    
    # Construir la lista de bases de datos Glue utilizando las variables de entorno
    glue_databases = [
        f"glue_database_ingest-service-1_{os.getenv('DYNAMODB_TABLE_1_PROD')}_prod",
        f"glue_database_ingest-service-2_{os.getenv('DYNAMODB_TABLE_2_PROD')}_prod",
        f"glue_database_ingest-service-3_{os.getenv('DYNAMODB_TABLE_3_PROD')}_prod",
        f"glue_database_ingest-service-4_{os.getenv('DYNAMODB_TABLE_4_PROD')}_prod",
        f"glue_database_ingest-service-5_{os.getenv('DYNAMODB_TABLE_5_PROD')}_prod"
    ]
    
    glue_crawlers = [
        f"crawler_ingest-service-1_{os.getenv('DYNAMODB_TABLE_1_PROD')}_prod",
        f"crawler_ingest-service-2_{os.getenv('DYNAMODB_TABLE_2_PROD')}_prod",
        f"crawler_ingest-service-3_{os.getenv('DYNAMODB_TABLE_3_PROD')}_prod",
        f"crawler_ingest-service-4_{os.getenv('DYNAMODB_TABLE_4_PROD')}_prod",
        f"crawler_ingest-service-5_{os.getenv('DYNAMODB_TABLE_5_PROD')}_prod"
    ]

if __name__ == "__main__":
    main()