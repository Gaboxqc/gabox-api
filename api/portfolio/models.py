from enum import Enum
from typing import Annotated, Optional, List

from pydantic import StringConstraints
from sqlmodel import SQLModel, Field, Relationship

class ProjectType(str, Enum):
    WEB = "Web Application"
    MOBILE = "Mobile App"
    DESKTOP = "Desktop App"
    COMPONENT = "React Component"
    API = "API"

class DifficultyLevel(str, Enum):
    JUNIOR = "Junior"
    INTERMEDIATE = "Intermediate"
    ADVANCED = "Advanced"
    EXPERT = "Expert"

class Category(str, Enum):
    FRONTEND = "Frontend"
    BACKEND = "Backend"
    DB = "Data Base"

LanguageCode = Annotated[str, StringConstraints(min_length=2, max_length=2, to_lower=True)]

class ProjectTag(SQLModel, table=True):
    __tablename__: str = "portfolio_project_tag"

    project_id: int = Field(foreign_key="portfolio_project.id", primary_key=True)
    tag_id: int = Field(foreign_key="portfolio_tag.id", primary_key=True)

class CertificateTag(SQLModel, table=True):
    __tablename__: str = "portfolio_certificate_tag"

    certificate_id: int = Field(foreign_key="portfolio_certificate.id", primary_key=True)
    tag_id: int = Field(foreign_key="portfolio_tag.id", primary_key=True)

class TagBase(SQLModel):
    name: str = Field(unique=True, index=True, min_length=1)

class TagCreate(TagBase):
    pass

class TagUpdate(SQLModel):
    name: Optional[str] = None

class Tag(TagBase, table=True):
    __tablename__: str = "portfolio_tag"

    id: Optional[int] = Field(default=None, primary_key=True)

    projects: List["Project"] = Relationship(back_populates="tags", link_model=ProjectTag)
    certificates: List["Certificate"] = Relationship(back_populates="tags", link_model=CertificateTag)

class ProjectBase(SQLModel):
    year: int = Field(index=True)
    type: ProjectType = Field(default=ProjectType.WEB)
    difficulty: DifficultyLevel = Field(default=DifficultyLevel.JUNIOR)
    image_url: Optional[str] = None
    git_url: Optional[str] = None
    deploy_url: Optional[str] = None

class ProjectCreate(ProjectBase):
    pass

class ProjectUpdate(SQLModel):
    year: Optional[int] = None
    type: Optional[ProjectType] = None
    difficulty: Optional[DifficultyLevel] = None
    image_url: Optional[str] = None
    git_url: Optional[str] = None
    deploy_url: Optional[str] = None

class Project(ProjectBase, table=True):
    __tablename__: str = "portfolio_project"

    id: Optional[int] = Field(default=None, primary_key=True)

    translations: List["ProjectTranslation"] = Relationship(back_populates="project", cascade_delete=True)
    tags: List[Tag] = Relationship(back_populates="projects", link_model=ProjectTag)

class ProjectTranslationBase(SQLModel):
    language_code: LanguageCode
    title: str = Field(min_length=2)
    description: str = Field(min_length=10)

class ProjectTranslationCreate(ProjectTranslationBase):
    project_id: int

class ProjectTranslationUpdate(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None

class ProjectTranslation(ProjectTranslationBase, table=True):
    __tablename__: str = "portfolio_project_translation"

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="portfolio_project.id", ondelete="CASCADE")

    project: Project = Relationship(back_populates="translations")

class AcademyBase(SQLModel):
    name: str = Field(min_length=2, index=True)

class AcademyCreate(AcademyBase):
    pass

class AcademyUpdate(AcademyBase):
    pass

class Academy(AcademyBase, table=True):
    __tablename__: str = "portfolio_academy"

    id: Optional[int] = Field(default=None, primary_key=True)

    certificates: List["Certificate"] = Relationship(back_populates="academy")

class CertificateBase(SQLModel):
    title: str = Field(min_length=3)
    year: int = Field(index=True)
    validation_serial: Optional[str] = Field(default=None, unique=True)
    url: Optional[str] = None
    isMain: bool = Field(default=False)
    isVerified: bool = Field(default=False)
    academy_id: int = Field(foreign_key="portfolio_academy.id")

class CertificateCreate(CertificateBase):
    pass

class CertificateUpdate(SQLModel):
    title: Optional[str] = None
    year: Optional[int] = None
    validation_serial: Optional[str] = None
    url: Optional[str] = None
    isMain: Optional[bool] = None
    isVerified: Optional[bool] = None
    academy_id: Optional[int] = None

class Certificate(CertificateBase, table=True):
    __tablename__: str = "portfolio_certificate"

    id: Optional[int] = Field(default=None, primary_key=True)

    academy: Academy = Relationship(back_populates="certificates")
    tags: List[Tag] = Relationship(back_populates="certificates", link_model=CertificateTag)