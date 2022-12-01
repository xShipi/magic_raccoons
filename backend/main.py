from functools import lru_cache
import json
import subprocess
import sys
from typing import Optional

import requests
from fastapi import Depends, FastAPI, HTTPException, status, File, UploadFile
from fastapi.security import OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from jose.constants import ALGORITHMS
from pydantic import BaseModel

from config import Settings

from sqlalchemy.orm import Session
import crud, models, schemas
from database import SessionLocal, engine

import shutil
models.Base.metadata.create_all(bind=engine)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@lru_cache()
def get_settings():
    return Settings()


class TokenData(BaseModel):
    username: Optional[str] = None


class User(BaseModel):
    username: str


# Why doing this?
# Because we want to fetch public key on start
# Later we would verify incoming JWT tokens
try:
    r = requests.get(get_settings().keycloak_realm_url,
                     timeout=3)
    r.raise_for_status()
    response_json = r.json()
except requests.exceptions.HTTPError as errh:
    print("Http Error:", errh)
    sys.exit(1)
except requests.exceptions.ConnectionError as errc:
    print("Error Connecting:", errc)
    sys.exit(1)
except requests.exceptions.Timeout as errt:
    print("Timeout Error:", errt)
    sys.exit(1)
except requests.exceptions.RequestException as err:
    print("OOps: Something Else", err)
    sys.exit(1)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
SECRET_KEY = f'-----BEGIN PUBLIC KEY-----\r\n{response_json["public_key"]}\r\n-----END PUBLIC KEY-----'
app = FastAPI()

origins = [
    get_settings().ui_url,
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EXTENSIONS = set(['caff'])

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHMS.RS256],
                             options={"verify_signature": True, "verify_aud": False, "exp": True})
        username: str = payload.get("preferred_username")
        print(token, payload)

        token_data = TokenData(username=json.dumps(payload))
    except JWTError as e:
        print(e)
        raise credentials_exception
    return token_data


async def get_current_active_user(current_user: User = Depends(get_current_user)):
    return current_user

@app.get("/api/logs")
async def get_logs(db:Session=Depends(get_db)):
    return crud.get_logs(db)

@app.post("/api/logs")
async def create_log(log:schemas.Log,db:Session=Depends(get_db)):
    return crud.create_log(log,db)

@app.get("/api/users")
async def get_users(db:Session=Depends(get_db)):
    return crud.get_logs(db)

@app.post("/api/users")
async def create_user(user:schemas.User,db:Session=Depends(get_db)):
    return crud.create_user(user,db=db)


@app.get("/users/me")
async def get_user_id_by_username(username:str,db:Session=Depends(get_db)):
    return crud.get_user_id_by_username(username,db)


@app.get("/api")
async def read_caffs(db:Session=Depends(get_db)):
    caffs = crud.get_caffs(db)
    return caffs


@app.post("/api")
async def create_caff(caff:schemas.CaffBase,db:Session=Depends(get_db)):
    return crud.create_caff(db=db,caff=caff)

@app.get("/api/")
async def read_caffs_with_comments(db:Session=Depends(get_db)):
    caffs = crud.get_caffs(db)
    ret:list=[]
    for x in caffs:
        comments:list=crud.get_comments_by_collection_id(x.id,db=db)
        element=vars(x)
        element["comments"]=[]
        element["comments"]+=comments
        ret.append(element)
    return ret

@app.post("/api/{caff_id}/comments")
async def create_comment_to_caff(caff_id:int,comment:schemas.CommentBase,db:Session=Depends(get_db)):
    caff = crud.get_caff_by_id(caff_id,db=db,skip=0)
    if (caff==None):
        raise HTTPException(status_code=400, detail="There is not a Caff with id: "+str(caff_id))
    return crud.create_comment(db=db,comment=comment,collection_id=caff_id)

@app.get("/api/{caff_id}")
async def read_caff_by_id_with_comments(caff_id:int,db:Session=Depends(get_db)):
    caff = crud.get_caff_by_id(caff_id,db=db,skip=0)
    if (caff==None):
        raise HTTPException(status_code=400, detail="There is not a Caff with id: "+str(caff_id))
    comments = crud.get_comments_by_collection_id(collection_id=caff_id,db=db)
    caff_dict=vars(caff)
    print(type(comments))
    print(comments)
    caff_dict['comments']=[]
    caff_dict["comments"]+=comments
    return caff_dict


@app.get("/api/{caff_id}/comments/{comment_id}")
async def get_comment_by_id(caff_id:int,comment_id:int,db:Session=Depends(get_db)):
    caff = crud.get_caff_by_id(caff_id,db=db,skip=0)
    if (caff==None):
        raise HTTPException(status_code=400, detail="There is not a Caff with id: "+str(caff_id))
    comment = crud.get_comment_by_id(comment_id,db)
    if (comment==None):
        raise HTTPException(status_code=400, detail="There is not a comment with id: "+str(comment_id))
    return comment

@app.put("/api/{caff_id}/comments/{comment_id}")
async def update_comment_by_id(caff_id:int,comment_id:int,comment:schemas.CommentBase,db:Session=Depends(get_db)):
    caff = crud.get_caff_by_id(caff_id,db=db,skip=0)
    if (caff==None):
        raise HTTPException(status_code=400, detail="There is not a Caff with id: "+str(caff_id))
    comment_ret = crud.get_comment_by_id(comment_id,db)
    if (comment_ret==None):
        raise HTTPException(status_code=400, detail="There is not a comment with id: "+str(comment_id))
    comment_updated= crud.update_comment_by_id(comment_id=comment_id,comment=comment,db=db)
    return comment_updated

@app.delete("/api/{caff_id}/comments/{comment_id}")
async def delete_comment_by_id(caff_id:int,comment_id:int,db:Session=Depends(get_db)):
    caff = crud.get_caff_by_id(caff_id,db=db,skip=0)
    if (caff==None):
        raise HTTPException(status_code=400, detail="There is not a Caff with id: "+str(caff_id))
    comment = crud.get_comment_by_id(comment_id,db)
    if (comment==None):
        raise HTTPException(status_code=400, detail="There is not a comment with id: "+str(comment_id))
    return crud.delete_comment_by_id(comment_id,db)

@app.post("/upload_file")
async def create_upload_file(file: UploadFile = File(...)):
    if not file:
        return {"message": "No upload file sent"}
    else:
        if allowed_file(file.filename)==True:
            with open(f'./data/{file.filename}','wb')as buffer:
                shutil.copyfileobj(file.file,buffer)
            #TODO, filename
            parse_caff("1.caff")
            return {"message":"Uploaded successfully"}
        else:
            return{"message":"Illegal file extension"}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

@app.get("/process")
def parse_caff_file():
    return subprocess.run(args=['ls', '-l'])

#TODO: filename-et megcsinálni, és csak akkor működik, ha linux alatt fut a backend és linux alatt lett fordítva make all-al a caff parser
def parse_caff(filename:str):
    subprocess.run("../caff-parser/caff_parser ./data/1.caff ./result",shell=True)