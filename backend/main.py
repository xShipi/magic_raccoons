from datetime import datetime
from enum import Enum
from functools import lru_cache
import subprocess

from fastapi import Depends, FastAPI, HTTPException, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from auth import Auth, Role, User

from config import Settings

from sqlalchemy.orm import Session
import crud
import models
import schemas
from database import SessionLocal, engine

from os import makedirs, listdir, remove
from uuid import uuid4
from json import load
from re import match
from PIL import Image
from starlette.responses import FileResponse
import shutil


models.Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@lru_cache()
def get_settings():
    return Settings()


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
auth = Auth(get_settings().keycloak_realm_url)


async def save_user(user: User, db: Session):
    db_user = crud.get_user_by_userid(user.id, db)
    if db_user is None:
        db_user = crud.create_user(db=db, user=user)
    return db_user


async def get_session_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    user = await auth.get_user(token)
    if user is None:
        raise HTTPException(
            status_code=401, detail="Invalid authentication credentials")
    await save_user(user, db)
    return user

app = FastAPI()

app.mount("/preview", StaticFiles(directory="../data/preview"), name="preview")

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


class Loglevel(Enum):
    WARNING = "WARNING",
    ERROR = "ERROR"


class Action(Enum):
    GET = "GET",
    POST = "CREATED",
    PUT = "EDITED",
    DELETE = "DELTED"


class Logger:
    template_msg = "User with ID %s %s %s %s."

    def log(self, level: str, user_id: str, text: str = "", db: Session = Depends(get_db)):
        crud.create_log(schemas.Log(level=level, text=text,
                        date=datetime.now(), author_id=user_id), db=db)


@app.get("/api/logs")
async def get_logs(db: Session = Depends(get_db), user: User = Depends(get_session_user)):
    if user == None:
        Logger.log(Logger, level="WARNING", user_id=user.id,
                   text="User doesn't exist. with id"+user.id+".", db=db)
        raise HTTPException(status_code=401, detail="Invalid token")

    if user.role != Role.ADMIN:
        Logger.log(Logger, level="ERROR", user_id=user.id,
                   text="User is not an ADMIN id:"+user.id+".", db=db)
        raise HTTPException(status_code=403, detail="Forbidden")

    logs = crud.get_logs(db)
    ret_logs = []
    for log in logs:
        tmp_log = {"text": log.text, "level": log.level, "date": log.date}
        ret_logs.append(tmp_log)
    return ret_logs


@app.get("/api/users/me")
async def get_user_id_by_username(user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    tmp_user = crud.get_user_by_userid(user_id=user.id, db=db)
    if (tmp_user == None):
        Logger.log(Logger, level="WARNING", user_id=user.id,
                   text="User doesn't exist. with id"+user.id+".", db=db)
        crud.create_user(schemas.User(user_id=str(user.id),
                         username=str(user.name)), db=db)
    return user


@app.get("/api")
async def read_caffs(tag: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_session_user)):
    if tag is None:
        caffs = crud.get_caffs(db)
        return caffs
    caff_ids = crud.get_caff_ids_by_tag(tag, db)
    ret: list = []
    for x in caff_ids:
        caff_id = vars(x)
        caff = crud.get_caff_by_id(caff_id["collection_id"], db)
        if caff not in ret:
            ret.append(caff)
    return ret


@app.get("/api/")
async def read_caffs_with_comments(db: Session = Depends(get_db), user: User = Depends(get_session_user)):
    caffs = crud.get_caffs(db)
    ret: list = []
    for x in caffs:
        comments: list = crud.get_comments_by_collection_id(x.id, db=db)
        element = vars(x)
        element["comments"] = []
        element["comments"] += comments
        ret.append(element)
    return ret


@app.get("/api/{caff_id}")
async def read_caff_by_id_with_comments(caff_id: int, db: Session = Depends(get_db), user: User = Depends(get_session_user)):
    caff = crud.get_caff_by_id(caff_id, db=db)
    if (caff == None):
        raise HTTPException(
            status_code=400, detail="There is not a Caff with id: "+str(caff_id))
    comments = crud.get_comments_by_collection_id(collection_id=caff_id, db=db)
    comment_dict = []
    for comment in comments:
        author = crud.get_user_by_userid(db=db, user_id=comment.author_id)
        if (author == None):
            username = "Anonymus"
        else:
            username = author.username
        comment_element = {"text": comment.text, "username": username,
                           "date": comment.date, "id": comment.id}
        comment_dict.append(comment_element)
    caff_dict = vars(caff)
    caff_dict["comments"] = []
    caff_dict["comments"] += comment_dict
    return caff_dict


@app.post("/api/{caff_id}/comments")
async def create_comment_to_caff(caff_id: int, comment: schemas.CommentBase, db: Session = Depends(get_db), user: User = Depends(get_session_user)):
    Logger.log(Logger, level="INFO", user_id=user.id,
               text="User added comment with the text of: "+comment.text+".", db=db)
    caff = crud.get_caff_by_id(caff_id, db=db)
    comment.author_id = user.id
    if (caff == None):
        Logger.log(Logger, level="ERROR", user_id=user.id,
                   text="User added comment, but CAFF doesnt exists with id: "+caff_id+".", db=db)
        raise HTTPException(
            status_code=400, detail="There is not a Caff with id: "+str(caff_id))
    return crud.create_comment(db=db, comment=comment, collection_id=caff_id)


@app.get("/download_caff/{caff_id}", response_class=FileResponse)
async def download_caff(caff_id: int, db: Session = Depends(get_db), user: User = Depends(get_session_user)):
    Logger.log(Logger, level="INFO", user_id=user.id,
               text="User downloads CAFF with id:"+str(caff_id)+".", db=db)
    caff = crud.get_caff_by_id(caff_id, db=db)
    if (caff == None):
        Logger.log(Logger, level="ERROR", user_id=user.id, text="User downloads CAFF with id:" +
                   str(caff_id)+", but CAFF not exists with the ID listed.", db=db)
        raise HTTPException(
            status_code=400, detail="There is not a Caff with id: "+str(caff_id))
    filename = caff.rawfile.split('/')[-1]
    return FileResponse(caff.rawfile, filename=filename)


@app.put("/api/{caff_id}/comments/{comment_id}")
async def update_comment_by_id(caff_id: int, comment_id: int, comment: schemas.CommentUpdate, db: Session = Depends(get_db), user: User = Depends(get_session_user)):
    if (user.role != Role.ADMIN):
        Logger.log(Logger, level="WARNING", user_id=user.id,
                   text="User tries to edit comment, but is not an ADMIN. user_id:"+user.id+".", db=db)
        raise HTTPException(
            status_code=403, detail="ADMIN only functionality")
    caff = crud.get_caff_by_id(caff_id, db=db)
    if (caff == None):
        Logger.log(Logger, level="ERROR", user_id=user.id,
                   text="User downloads CAFF with id:"+str(caff_id)+", but CAFF doesnt exists.", db=db)
        raise HTTPException(
            status_code=400, detail="There is not a Caff with id: "+str(caff_id))
    comment_ret = crud.get_comment_by_id(comment_id, db)
    if (comment_ret == None):
        Logger.log(Logger, level="ERROR", user_id=user.id,
                   text="User tries to edit comment, but does not exists. Given Comment.id:"+comment_id+".", db=db)
        raise HTTPException(
            status_code=400, detail="There is not a comment with id: "+str(comment_id))
    edited_comment = comment_ret
    edited_comment.text = comment.text
    comment_updated = crud.update_comment_by_id(
        comment_id=comment_id, comment=edited_comment, db=db)
    return comment_updated


@app.delete("/api/{caff_id}")
async def delete_caff_by_id(caff_id: int, db: Session = Depends(get_db), user: User = Depends(get_session_user)):
    if (user.role != Role.ADMIN):
        Logger.log(Logger, level="WARNING", user_id=user.id,
                   text="User tries to delete CAFF, but is not an ADMIN. user_id:"+user.id+".", db=db)
        raise HTTPException(
            status_code=403, detail="ADMIN only functionality")
    caff = crud.get_caff_by_id(caff_id, db=db)
    if (caff == None):
        Logger.log(Logger, level="ERROR", user_id=user.id,
                   text="User tries to delete CAFF with id:"+str(caff_id)+", but CAFF doesnt exists.", db=db)
        raise HTTPException(
            status_code=400, detail="There is not a Caff with id: "+str(caff_id))
    is_successful = crud.delete_caff_by_id(caff_id, db)
    if not is_successful:
        Logger.log(Logger, level="ERROR", user_id=user.id,
                   text="Could not delete Caff with id: "+str(caff_id), db=db)
        raise HTTPException(
            status_code=400, detail="Could not delete Caff with id: "+str(caff_id))


@app.delete("/api/{caff_id}/comments/{comment_id}")
async def delete_comment_by_id(caff_id: int, comment_id: int, db: Session = Depends(get_db), user: User = Depends(get_session_user)):
    if (user.role != Role.ADMIN):
        Logger.log(Logger, "WARNING", user.id,
                   "User tries to delete comment, but is not an ADMIN. User.id:"+user.id, db=db)
        raise HTTPException(
            status_code=403, detail="ADMIN only functionality")
    caff = crud.get_caff_by_id(caff_id, db=db)
    if (caff == None):
        Logger.log(Logger, level="ERROR", user_id=user.id,
                   text="User tries to delete comment for Caff.id:"+str(caff_id)+", but CAFF doesnt exists.", db=db)
        raise HTTPException(
            status_code=400, detail="There is not a Caff with id: "+str(caff_id))
    comment = crud.get_comment_by_id(comment_id, db)
    if (comment == None):
        Logger.log(Logger, level="ERROR", user_id=user.id, text="User tries to delete comment for Caff.id:" +
                   str(caff_id)+", but comment doesnt exists.", db=db)
        raise HTTPException(
            status_code=400, detail="There is not a comment with id: "+str(comment_id))
    return crud.delete_comment_by_id(comment_id, db)


@app.post("/upload_file")
async def create_upload_file(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_session_user)):
    Logger.log(Logger, "INFO", user_id=user.id,
               text="User tries to upload file", db=db)
    if not file:
        Logger.log(Logger, level="ERROR", user_id=user.id,
                   text="Given data is not a file", db=db)
        return {"message": "No upload file sent"}
    else:
        if allowed_file(file.filename) == True:
            folder_id = str(uuid4())
            folder = "/caff/data/out/"+folder_id
            filename_with_extension = "source.caff"
            makedirs(folder)
            with open(f'{folder}/{filename_with_extension}', 'wb')as buffer:
                shutil.copyfileobj(file.file, buffer)
            parse_caff(db=db, filename=filename_with_extension,
                       dir=folder, user_id=user.id)
            return {"message": "Uploaded successfully"}
        else:
            Logger.log(Logger, level="ERROR", user_id=user.id,
                       text="Incorrect file extension: not .caff.", db=db)
            return {"message": "Illegal file extension"}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_caff(db: Session, filename: str, dir: str, user_id: str):
    preview_path = '/caff/data/preview/'

    args = ["/caff/parser/caff_parser", dir+'/'+filename,
            dir]

    cmd = " ".join(args)

    output = subprocess.run(cmd, shell=True)

    if output.returncode != 0:
        remove("/caff/backend/data/"+filename)
        Logger.log(Logger, "ERROR", user_id,
                   "Couldn't parse caff file.", db=db)
        raise HTTPException(
            status_code=422, detail="The content of the uploaded file did not fit the CAFF file format. Upload did not complete.")

    f = open(dir+"/metadata.json", "r")
    metadata = load(f)
    f.close()

    year = metadata["credits"]["year"]
    day = metadata["credits"]["day"]
    hour = metadata["credits"]["hour"]
    month = metadata["credits"]["month"]
    creator = metadata["credits"]["creator"]
    creator_len = len(creator)

    animations = metadata["animation"]

    caff = crud.create_caff(db=db, caff=schemas.CaffBase(year=year, month=month, day=day,
                            hour=hour, minute=-1, creatorlen=creator_len, creator=creator, rawfile=dir+'/'+filename))

    for i in animations:
        duration = i["duration"]
        width = i["width"]
        height = i["height"]
        caption = i["caption"]
        tags = ';'.join(i["tags"])
        crud.create_ciff(db=db, ciff=schemas.CiffCreate(width=width, height=height,
                         collection_id=caff.id, duration=duration, caption=caption, tags=tags))

    create_preview_gif(caff.id, preview_path, dir+'/')


def create_preview_gif(caff_id, preview_path, gen_path):
    files = listdir(gen_path)

    tgas = []

    preview_names = "preview\d\d*.tga"

    for filename in files:
        if match(preview_names, filename):
            tga = Image.open(gen_path + filename)
            tgas.append(tga)

    preview_filepath = preview_path + str(caff_id) + '.gif'
    tgas[0].save(preview_filepath, save_all=True,
                 append_images=tgas[1:], optimize=False, duration=1000, loop=0)
