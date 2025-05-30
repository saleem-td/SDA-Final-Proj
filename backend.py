from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Request, status
from fastapi.security import OAuth2AuthorizationCodeBearer
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from openai import OpenAI
from fastapi.responses import StreamingResponse, RedirectResponse
from dotenv import load_dotenv
import json
import psycopg2
import os
import uuid
import msal
import requests
from psycopg2.extras import RealDictCursor
from typing import List, Optional, Dict, Any
from langchain_community.document_loaders import PyPDFLoader
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_chroma import Chroma
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.chains import create_history_aware_retriever, create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.messages import HumanMessage, AIMessage
from azure.storage.blob import BlobClient
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
import chromadb
from langdetect import detect
from flask import session, redirect, request, url_for


load_dotenv()

keyVaultName = os.environ["KEY_VAULT_NAME"]
KVUri = f"https://{keyVaultName}.vault.azure.net"

credential = DefaultAzureCredential()
client = SecretClient(vault_url=KVUri, credential=credential)

# Database and storage configuration
DB_NAME = client.get_secret('PROJ-DB-NAME').value
DB_USER = client.get_secret('PROJ-DB-USER').value
DB_PASSWORD = client.get_secret('PROJ-DB-PASSWORD').value
DB_HOST = client.get_secret('PROJ-DB-HOST').value
DB_PORT = client.get_secret('PROJ-DB-PORT').value
OPENAI_API_KEY = client.get_secret('PROJ-OPENAI-API-KEY').value
AZURE_STORAGE_SAS_URL = client.get_secret('PROJ-AZURE-STORAGE-SAS-URL').value
AZURE_STORAGE_CONTAINER = client.get_secret('PROJ-AZURE-STORAGE-CONTAINER').value
CHROMADB_HOST = client.get_secret('PROJ-CHROMADB-HOST').value
CHROMADB_PORT = client.get_secret('PROJ-CHROMADB-PORT').value

# Microsoft Entra External ID configuration

ENTRA_TENANT_ID         = client.get_secret('PROJ-ENTRA-TENANT-ID').value
ENTRA_TENANT_NAME       = client.get_secret('PROJ-ENTRA-TENANT-NAME').value
ENTRA_CLIENT_ID         = client.get_secret('PROJ-ENTRA-CLIENT-ID').value
ENTRA_CLIENT_SECRET     = os.environ.get("ENTRA_CLIENT_SECRET")
ENTRA_POLICY_ID         = client.get_secret('PROJ-ENTRA-POLICY-ID').value
ENTRA_AUTHORITY_DOMAIN  = client.get_secret('PROJ-ENTRA-AUTHORITY-DOMAIN').value


# Access environment variables
tenant_name             = os.environ.get("ENTRA_TENANT_NAME")
client_id               = os.environ.get("ENTRA_CLIENT_ID")
client_secret           = os.environ.get("ENTRA_CLIENT_SECRET")
policy_id               = os.environ.get("ENTRA_POLICY_ID")
authority_domain        = os.environ.get("ENTRA_AUTHORITY_DOMAIN")





# Arabic language support
SUPPORT_ARABIC = os.environ.get("SUPPORT_ARABIC", "false").lower() == "true"

DB_CONFIG = {
    "dbname": DB_NAME,
    "user": DB_USER,
    "password": DB_PASSWORD,
    "host": DB_HOST,
    "port": DB_PORT,
}

client = OpenAI(api_key=OPENAI_API_KEY)

model = "gpt-3.5-turbo"

# LangChain setup
embedding_function = OpenAIEmbeddings(api_key=OPENAI_API_KEY)
chroma_client = chromadb.HttpClient(host=CHROMADB_HOST, port=CHROMADB_PORT)
collection = chroma_client.get_or_create_collection("langchain")
vectorstore = Chroma(
            client=chroma_client,
            collection_name="langchain",
            embedding_function=embedding_function,
)

# Use a multilingual embedding model if Arabic is supported
if SUPPORT_ARABIC:
    embedding_function = OpenAIEmbeddings(
        api_key=OPENAI_API_KEY,
        model="text-embedding-ada-002"  # This model has better multilingual support
    )
    # Create a separate collection for Arabic content
    arabic_collection = chroma_client.get_or_create_collection("langchain_arabic")
    arabic_vectorstore = Chroma(
        client=chroma_client,
        collection_name="langchain_arabic",
        embedding_function=embedding_function,
    )

storage_account_sas_url = AZURE_STORAGE_SAS_URL
storage_container_name = AZURE_STORAGE_CONTAINER
storage_resource_uri = storage_account_sas_url.split('?')[0]
token = storage_account_sas_url.split('?')[1]

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# OAuth2 scheme for Microsoft Entra External ID authentication
oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl=f"https://{ENTRA_AUTHORITY_DOMAIN}/{ENTRA_TENANT_NAME}/oauth2/v2.0/authorize",
    tokenUrl=f"https://{ENTRA_AUTHORITY_DOMAIN}/{ENTRA_TENANT_NAME}/oauth2/v2.0/token",
    scopes={"https://graph.microsoft.com/User.Read": "User.Read"},
)

# Request models
class ChatRequest(BaseModel):
    messages: List[dict]
    language: Optional[str] = "English"

class SaveChatRequest(BaseModel):
    chat_id: str
    chat_name: str
    messages: List[dict]
    pdf_name: Optional[str] = None
    pdf_path: Optional[str] = None
    pdf_uuid: Optional[str] = None
    language: Optional[str] = "English"

class DeleteChatRequest(BaseModel):
    chat_id: str

class RAGChatRequest(BaseModel):
    messages: List[dict]
    pdf_uuid: str
    language: Optional[str] = "English"

class TokenRequest(BaseModel):
    code: str
    redirect_uri: str

# Dependency to manage database connection
def get_db():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        yield conn
    finally:
        conn.close()

# Validate token from Microsoft Entra External ID
async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        # Validate token with Microsoft
        app = msal.ConfidentialClientApplication(
            ENTRA_CLIENT_ID,
            authority=f"https://{ENTRA_AUTHORITY_DOMAIN}/{ENTRA_TENANT_NAME}",
            client_credential=ENTRA_CLIENT_SECRET,
        )
        
        # Verify the token
        result = app.acquire_token_by_authorization_code(
            token,
            scopes=["https://graph.microsoft.com/User.Read"],
        )
        
        if "error" in result:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication credentials: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

# Authentication endpoints

# Configuration values from Azure portal
CLIENT_ID = "d478dedc-ddd5-48be-8fd5-338b09c1adbd"  # Your application's client ID
CLIENT_SECRET = client_secret # Your application's client secret
AUTHORITY = "https://login.microsoftonline.com/d3af128d-3b0d-4d96-bedd-31fd6a67d75c"  # Your tenant ID
REDIRECT_URI = "http://localhost:5000/getAToken"  # The redirect URI you registered
SCOPE = ["User.ReadBasic.All"]  # The permissions you need

# Initialize MSAL application
app_config = {
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "authority": AUTHORITY,
    "redirect_uri": REDIRECT_URI,
}

# Create a confidential client application
app = msal.ConfidentialClientApplication(
    app_config["client_id"], 
    authority=app_config["authority"],
    client_credential=app_config["client_secret"]
)

@app.get("/login")
async def login():
    # Get the authorization URL and state
    auth_url = app.get_authorization_request_url(
        SCOPE,
        redirect_uri=app_config["redirect_uri"],
        state="your_state_value"
    )
    return RedirectResponse(url=auth_url)

@app.get("/getAToken")
async def get_token(code: str = None, state: str = None):
    if code:
        # Exchange the authorization code for a token
        result = app.acquire_token_by_authorization_code(
            code,
            scopes=SCOPE,
            redirect_uri=app_config["redirect_uri"]
        )
        
        if "error" in result:
            return {"error": result.get("error"), "error_description": result.get("error_description")}
        
        # Store the token in your session or database
        # session["token"] = result
        
        # Redirect to your application's home page
        return RedirectResponse(url="/")
    else:
        return {"error": "No code received"}

@app.get("/logout")
async def logout():
    # Clear the user's session
    # session.clear()
    
    # Redirect to Microsoft logout endpoint
    return RedirectResponse(url=f"{AUTHORITY}/oauth2/v2.0/logout?post_logout_redirect_uri={app_config['redirect_uri']}")

@app.get("/auth/callback")
async def auth_callback(code: str, request: Request):
    # Log that we received a callback
    print(f"Received callback with code: {code[:5]}...")
    
    try:
        # Exchange code for token
        app_instance = msal.ConfidentialClientApplication(
            ENTRA_CLIENT_ID,
            authority=f"https://{ENTRA_AUTHORITY_DOMAIN}/{ENTRA_TENANT_NAME}",
            client_credential=ENTRA_CLIENT_SECRET,
        )
        
        result = app_instance.acquire_token_by_authorization_code(
            code,
            scopes=["https://graph.microsoft.com/User.Read"],
            redirect_uri="https://divstar.digital/auth/callback"
        )
        
        if "error" in result:
            print(f"Error in token acquisition: {result.get('error_description')}")
            return HTMLResponse(content=f"""
            <html>
                <body>
                    <h1>Authentication Error</h1>
                    <p>{result.get('error_description')}</p>
                    <a href="/">Return to home</a>
                </body>
            </html>
            """)
        
        # Successful authentication - redirect to main app with token
        return HTMLResponse(content=f"""
        <html>
            <body>
                <h1>Authentication Successful</h1>
                <p>Redirecting to application...</p>
                <script>
                    // Store token in localStorage
                    localStorage.setItem('auth_token', '{result.get("access_token")}');
                    localStorage.setItem('user_name', '{result.get("id_token_claims", {}).get("name", "User")}');
                    // Redirect to main application
                    window.location.href = '/';
                </script>
            </body>
        </html>
        """)
    except Exception as e:
        print(f"Exception in callback: {str(e)}")
        return HTMLResponse(content=f"""
        <html>
            <body>
                <h1>Authentication Error</h1>
                <p>An error occurred: {str(e)}</p>
                <a href="/">Return to home</a>
            </body>
        </html>
        """)

@app.post("/auth/token")
async def get_token(request: TokenRequest):
    try:
        # Exchange authorization code for tokens
        app = msal.ConfidentialClientApplication(
            ENTRA_CLIENT_ID,
            authority=f"https://{ENTRA_AUTHORITY_DOMAIN}/{ENTRA_TENANT_NAME}",
            client_credential=ENTRA_CLIENT_SECRET,
        )
        
        result = app.acquire_token_by_authorization_code(
            request.code,
            scopes=["https://graph.microsoft.com/User.Read"],
            redirect_uri=request.redirect_uri
        )
        
        if "error" in result:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=result.get("error_description", "Token acquisition failed"),
            )
        
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token acquisition failed: {str(e)}",
        )

@app.post("/chat/")
async def chat(request: ChatRequest):
    try:
        # Detect language if Arabic support is enabled
        language = request.language
        system_message = "You are a helpful assistant."
        
        if SUPPORT_ARABIC:
            # Try to detect language from the last message
            last_message = next((m for m in reversed(request.messages) if m["role"] == "user"), None)
            if last_message:
                try:
                    detected = detect(last_message["content"])
                    if detected == "ar":
                        language = "العربية"
                        system_message = "أنت مساعد مفيد. أجب على الأسئلة باللغة العربية."
                except:
                    pass
        
        # Add system message if not present
        messages = request.messages.copy()
        if not any(m["role"] == "system" for m in messages):
            messages.insert(0, {"role": "system", "content": system_message})
        
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )

        # Function to send out the stream data
        def stream_response():
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta

        # Use StreamingResponse to return
        return StreamingResponse(stream_response(), media_type="text/plain")
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/load_chat/")
async def load_chat(db: psycopg2.extensions.connection = Depends(get_db), user: Dict[str, Any] = Depends(get_current_user)):
    try:
        with db.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT id, name, file_path, pdf_name, pdf_path, pdf_uuid, language FROM advanced_chats ORDER BY last_update DESC")
            rows = cursor.fetchall()

        records = []
        for row in rows:
            chat_id, name, file_path, pdf_name, pdf_path, pdf_uuid, language = row["id"], row["name"], row["file_path"], row["pdf_name"], row["pdf_path"], row["pdf_uuid"], row.get("language", "English")

            blob_sas_url = f"{storage_resource_uri}/{storage_container_name}/{file_path}?{token}"
            blob_client = BlobClient.from_blob_url(blob_sas_url)

            if blob_client.exists():
                blob_data = blob_client.download_blob().readall()
                messages = json.loads(blob_data)
                records.append({
                    "id": chat_id, 
                    "chat_name": name, 
                    "messages": messages, 
                    "pdf_name": pdf_name, 
                    "pdf_path": pdf_path, 
                    "pdf_uuid": pdf_uuid,
                    "language": language
                })

        return records

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/save_chat/")
async def save_chat(request: SaveChatRequest, db: psycopg2.extensions.connection = Depends(get_db), user: Dict[str, Any] = Depends(get_current_user)):
    try:
        file_path = f"chat_logs/{request.chat_id}.json"
        
        blob_sas_url = f"{storage_resource_uri}/{storage_container_name}/{file_path}?{token}"
        blob_client = BlobClient.from_blob_url(blob_sas_url)
        messages_data = json.dumps(request.messages, ensure_ascii=False, indent=4)
        blob_client.upload_blob(messages_data, overwrite=True)
        
        # Get language if not specified
        language = request.language
        if SUPPORT_ARABIC and language == "English":
            # Try to detect language from the last message
            last_message = next((m for m in reversed(request.messages) if m["role"] == "user"), None)
            if last_message:
                try:
                    detected = detect(last_message["content"])
                    if detected == "ar":
                        language = "العربية"
                except:
                    pass
        
        # Insert or update database record
        with db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO advanced_chats (id, name, file_path, last_update, pdf_path, pdf_name, pdf_uuid, language)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s, %s)
                ON CONFLICT (id)
                DO UPDATE SET name = EXCLUDED.name, file_path = EXCLUDED.file_path, last_update = CURRENT_TIMESTAMP, 
                pdf_path = EXCLUDED.pdf_path, pdf_name = EXCLUDED.pdf_name, pdf_uuid = EXCLUDED.pdf_uuid, language = EXCLUDED.language
                """,
                (request.chat_id, request.chat_name, file_path, request.pdf_path, request.pdf_name, request.pdf_uuid, language),
            )
        db.commit()
        return {"message": "Chat saved successfully"}
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/delete_chat/")
async def delete_chat(request: DeleteChatRequest, db: psycopg2.extensions.connection = Depends(get_db), user: Dict[str, Any] = Depends(get_current_user)):
    try:
        # Retrieve the file path before deleting the record
        file_path = None
        with db.cursor() as cursor:
            cursor.execute("SELECT file_path, pdf_path FROM advanced_chats WHERE id = %s", (request.chat_id,))
            result = cursor.fetchone()
            if result:
                file_path = result[0]
                pdf_path = result[1]
            else:
                raise HTTPException(status_code=404, detail="Chat not found")

        # Delete the record from the database
        with db.cursor() as cursor:
            cursor.execute("DELETE FROM advanced_chats WHERE id = %s", (request.chat_id,))
        db.commit()
        
        if file_path:
            blob_sas_url = f"{storage_resource_uri}/{storage_container_name}/{file_path}?{token}"
            blob_client = BlobClient.from_blob_url(blob_sas_url)
            if blob_client.exists():
                blob_client.delete_blob()

        if pdf_path:
            blob_sas_url = f"{storage_resource_uri}/{storage_container_name}/{pdf_path}?{token}"
            blob_client = BlobClient.from_blob_url(blob_sas_url)
            if blob_client.exists():
                blob_client.delete_blob()

        return {"message": "Chat deleted successfully"}

    except HTTPException:
        # Reraise known exceptions
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/upload_pdf/")
async def upload_pdf(file: UploadFile = File(...), user: Dict[str, Any] = Depends(get_current_user)):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    try:
        pdf_uuid = str(uuid.uuid4())
        file_path = f"pdf_store/{pdf_uuid}_{file.filename}"
        os.makedirs("pdf_store", exist_ok=True)

        with open(file_path, "wb") as f:
            f.write(await file.read())
        blob_sas_url = f"{storage_resource_uri}/{storage_container_name}/{file_path}?{token}"
        blob_client = BlobClient.from_blob_url(blob_sas_url)
        blob_client.upload_blob(file_path, overwrite=True)

        # Load and process PDF
        loader = PyPDFLoader(file_path)
        documents = loader.load()
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        texts = text_splitter.split_documents(documents)

        # Detect language for each text chunk
        language = "English"
        if SUPPORT_ARABIC:
            # Sample the first few chunks to detect language
            sample_text = " ".join([texts[i].page_content for i in range(min(3, len(texts)))])
            try:
                detected = detect(sample_text)
                if detected == "ar":
                    language = "العربية"
            except:
                pass

        # Add to appropriate ChromaDB collection based on language
        if SUPPORT_ARABIC and language == "العربية":
            arabic_vectorstore.add_texts(
                [doc.page_content for doc in texts], 
                ids=[str(uuid.uuid4()) for _ in texts],
                metadatas=[{"pdf_uuid": pdf_uuid, "language": language} for _ in texts]    
            )
        else:
            vectorstore.add_texts(
                [doc.page_content for doc in texts], 
                ids=[str(uuid.uuid4()) for _ in texts],
                metadatas=[{"pdf_uuid": pdf_uuid, "language": language} for _ in texts]    
            )

        os.remove(file_path)

        return {"message": "File uploaded successfully", "pdf_path": file_path, "pdf_uuid": pdf_uuid, "language": language}
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@app.post("/rag_chat/")
async def rag_chat(request: RAGChatRequest, user: Dict[str, Any] = Depends(get_current_user)):
    # Determine language for the response
    language = request.language
    if SUPPORT_ARABIC:
        # Try to detect language from the last message
        last_message = next((m for m in reversed(request.messages) if m["role"] == "user"), None)
        if last_message:
            try:
                detected = detect(last_message["content"])
                if detected == "ar":
                    language = "العربية"
            except:
                pass
    
    # Select the appropriate vectorstore based on language
    selected_vectorstore = vectorstore
    if SUPPORT_ARABIC and language == "العربية":
        selected_vectorstore = arabic_vectorstore
    
    # Create retriever with language filter if needed
    retriever = selected_vectorstore.as_retriever(
        search_kwargs={"k": 5, "filter": {"pdf_uuid": request.pdf_uuid}}
    )
    
    # Set up the LLM with appropriate model
    llm = ChatOpenAI(model=model, api_key=OPENAI_API_KEY)
    
    ### Contextualize question ###
    contextualize_q_system_prompt = (
        "Given a chat history and the latest user question "
        "which might reference context in the chat history, "
        "formulate a standalone question which can be understood "
        "without the chat history. Do NOT answer the question, "
        "just reformulate it if needed and otherwise return it as is."
    )
    
    # Add Arabic instructions if needed
    if language == "العربية":
        contextualize_q_system_prompt = (
            "بالنظر إلى سجل المحادثة وآخر سؤال للمستخدم "
            "والذي قد يشير إلى سياق في سجل المحادثة، "
            "قم بصياغة سؤال مستقل يمكن فهمه "
            "بدون سجل المحادثة. لا تجب على السؤال، "
            "فقط أعد صياغته إذا لزم الأمر وإلا أعده كما هو."
        )
    
    contextualize_q_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", contextualize_q_system_prompt),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ]
    )
    history_aware_retriever = create_history_aware_retriever(
        llm, retriever, contextualize_q_prompt
    )

    ### Answer question ###
    system_prompt = (
        "You are an assistant for question-answering tasks. "
        "Use the following pieces of retrieved context to answer "
        "the question. If you don't know the answer, say that you "
        "don't know. Use three sentences maximum and keep the "
        "answer concise."
        "\n\n"
        "{context}"
    )
    
    # Add Arabic instructions if needed
    if language == "العربية":
        system_prompt = (
            "أنت مساعد لمهام الإجابة على الأسئلة. "
            "استخدم قطع السياق التالية المسترجعة للإجابة "
            "على السؤال. إذا كنت لا تعرف الإجابة، قل أنك "
            "لا تعرف. استخدم ثلاث جمل كحد أقصى واجعل "
            "الإجابة موجزة."
            "\n\n"
            "{context}"
        )
    
    qa_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            MessagesPlaceholder("chat_history"),
            ("human", "{input}"),
        ]
    )
    question_answer_chain = create_stuff_documents_chain(llm, qa_prompt)

    rag_chain = create_retrieval_chain(history_aware_retriever, question_answer_chain)

    chat_history = []

    user_input = request.messages[-1]["content"]
    
    for message in request.messages:
        if message["role"] == "user":
            chat_history.append(HumanMessage(content=message["content"]))
        if message["role"] == "assistant":
            chat_history.append(AIMessage(content=message["content"]))
    
    chain = rag_chain.pick("answer")

    stream = chain.stream({
        "chat_history": chat_history,
        "input": user_input
    })

    def stream_response():
        for chunk in stream:
            yield chunk

    # Use StreamingResponse to return
    return StreamingResponse(stream_response(), media_type="text/plain")