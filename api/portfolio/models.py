from typing import Optional, List
from sqlmodel import SQLModel, Field, Relationship


# ==============================================================================
# ARCHITECTURE NOTE:
# This file follows a strict Layered Dependency Order to prevent PyDantic
# 'NameError' and SQLAlchemy 'NoInspectionAvailable' errors.
# Order: Link Tables -> Independent Auxiliaries -> Translations -> Core Entities
# ==============================================================================


# ==============================================================================
# LAYER 1: MANY-TO-MANY LINK MODELS
# ==============================================================================

class ProjectTag(SQLModel, table=True):
    __tablename__: str = "portfolio_project_tag"
    project_id: int = Field(foreign_key="portfolio_project.id", ondelete="CASCADE", primary_key=True)
    tag_id: int = Field(foreign_key="portfolio_tag.id", ondelete="CASCADE", primary_key=True)


class CourseTag(SQLModel, table=True):
    __tablename__: str = "portfolio_course_tag"
    course_id: int = Field(foreign_key="portfolio_course.id", ondelete="CASCADE", primary_key=True)
    tag_id: int = Field(foreign_key="portfolio_tag.id", ondelete="CASCADE", primary_key=True)


class CertificationTag(SQLModel, table=True):
    __tablename__: str = "portfolio_certification_tag"
    certification_id: int = Field(foreign_key="portfolio_certification.id", ondelete="CASCADE", primary_key=True)
    tag_id: int = Field(foreign_key="portfolio_tag.id", ondelete="CASCADE", primary_key=True)


# ==============================================================================
# LAYER 2: INDEPENDENT AUXILIARY MODELS
# ==============================================================================

# ProjectType, DifficultyLevel, Category are seeded values —
# managed via migrations, not the API. No Create/Update schemas needed.

# --- Project Type ---
class ProjectTypeBase(SQLModel):
    name: str = Field(unique=True, index=True, min_length=1)


class ProjectTypeRead(ProjectTypeBase):
    id: int


class ProjectType(ProjectTypeBase, table=True):
    __tablename__: str = "portfolio_project_type"
    id: Optional[int] = Field(default=None, primary_key=True)
    projects: List["Project"] = Relationship(back_populates="project_type")


# --- Difficulty Level ---
class DifficultyLevelBase(SQLModel):
    name: str = Field(unique=True, index=True, min_length=1)


class DifficultyLevelRead(DifficultyLevelBase):
    id: int


class DifficultyLevel(DifficultyLevelBase, table=True):
    __tablename__: str = "portfolio_difficulty_level"
    id: Optional[int] = Field(default=None, primary_key=True)
    projects: List["Project"] = Relationship(back_populates="difficulty_level")


# --- Category ---
class CategoryBase(SQLModel):
    name: str = Field(unique=True, index=True, min_length=1)


class CategoryRead(CategoryBase):
    id: int


class Category(CategoryBase, table=True):
    __tablename__: str = "portfolio_category"
    id: Optional[int] = Field(default=None, primary_key=True)
    courses: List["Course"] = Relationship(back_populates="category")
    certifications: List["Certification"] = Relationship(back_populates="category")


# --- Language ---
class LanguageBase(SQLModel):
    code: str = Field(primary_key=True, min_length=2, max_length=2)
    name: str = Field(unique=True, min_length=2)


class LanguageRead(LanguageBase):
    pass


class Language(LanguageBase, table=True):
    __tablename__: str = "portfolio_language"
    project_translations: List["ProjectTranslation"] = Relationship(back_populates="language")
    course_translations: List["CourseTranslation"] = Relationship(back_populates="language")
    certification_translations: List["CertificationTranslation"] = Relationship(back_populates="language")


# --- Academy ---
class AcademyBase(SQLModel):
    name: str = Field(min_length=2, index=True)


class AcademyCreate(AcademyBase):
    pass


class AcademyUpdate(SQLModel):
    name: Optional[str] = None


class AcademyRead(AcademyBase):
    id: int


class Academy(AcademyBase, table=True):
    __tablename__: str = "portfolio_academy"
    id: Optional[int] = Field(default=None, primary_key=True)
    courses: List["Course"] = Relationship(back_populates="academy")
    certifications: List["Certification"] = Relationship(back_populates="academy")


# --- Tag ---
class TagBase(SQLModel):
    name: str = Field(unique=True, index=True, min_length=1)


class TagCreate(TagBase):
    pass


class TagUpdate(SQLModel):
    name: Optional[str] = None


class TagRead(TagBase):
    id: int


class Tag(TagBase, table=True):
    __tablename__: str = "portfolio_tag"
    id: Optional[int] = Field(default=None, primary_key=True)
    projects: List["Project"] = Relationship(back_populates="tags", link_model=ProjectTag)
    courses: List["Course"] = Relationship(back_populates="tags", link_model=CourseTag)
    certifications: List["Certification"] = Relationship(back_populates="tags", link_model=CertificationTag)


# ==============================================================================
# LAYER 3: TRANSLATION MODELS
# ==============================================================================

# --- Project Translation ---
class ProjectTranslationBase(SQLModel):
    title: str = Field(min_length=2)
    description: str = Field(min_length=10)


class ProjectTranslationCreate(ProjectTranslationBase):
    pass


class ProjectTranslationUpdate(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None


class ProjectTranslationRead(ProjectTranslationBase):
    language_code: str


class ProjectTranslation(ProjectTranslationBase, table=True):
    __tablename__: str = "portfolio_project_translation"
    language_code: str = Field(foreign_key="portfolio_language.code", ondelete="RESTRICT", primary_key=True)
    project_id: int = Field(foreign_key="portfolio_project.id", ondelete="CASCADE", primary_key=True)
    project: "Project" = Relationship(back_populates="translations")
    language: Language = Relationship(back_populates="project_translations")


# --- Course Translation ---
class CourseTranslationBase(SQLModel):
    title: str = Field(min_length=3)


class CourseTranslationCreate(CourseTranslationBase):
    language_code: str
    course_id: int


class CourseTranslationUpdate(SQLModel):
    title: Optional[str] = None


class CourseTranslationRead(CourseTranslationBase):
    language_code: str


class CourseTranslation(CourseTranslationBase, table=True):
    __tablename__: str = "portfolio_course_translation"
    language_code: str = Field(foreign_key="portfolio_language.code", ondelete="RESTRICT", primary_key=True)
    course_id: int = Field(foreign_key="portfolio_course.id", ondelete="CASCADE", primary_key=True)
    course: "Course" = Relationship(back_populates="translations")
    language: Language = Relationship(back_populates="course_translations")


# --- Certification Translation ---
class CertificationTranslationBase(SQLModel):
    title: str = Field(min_length=3)


class CertificationTranslationCreate(CertificationTranslationBase):
    language_code: str
    certification_id: int


class CertificationTranslationUpdate(SQLModel):
    title: Optional[str] = None


class CertificationTranslationRead(CertificationTranslationBase):
    language_code: str


class CertificationTranslation(CertificationTranslationBase, table=True):
    __tablename__: str = "portfolio_certification_translation"
    language_code: str = Field(foreign_key="portfolio_language.code", ondelete="RESTRICT", primary_key=True)
    certification_id: int = Field(foreign_key="portfolio_certification.id", ondelete="CASCADE", primary_key=True)
    certification: "Certification" = Relationship(back_populates="translations")
    language: Language = Relationship(back_populates="certification_translations")


# ==============================================================================
# LAYER 4: CORE ENTITIES & COMPLETE READ SCHEMAS
# ==============================================================================

# --- Core Entity: Project ---
class ProjectBaseFlat(SQLModel):
    year: int = Field(index=True)
    image_url: Optional[str] = None
    git_url: Optional[str] = None
    deploy_url: Optional[str] = None


class ProjectBase(ProjectBaseFlat):
    project_type_id: int = Field(foreign_key="portfolio_project_type.id")
    difficulty_level_id: int = Field(foreign_key="portfolio_difficulty_level.id")


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(SQLModel):
    year: Optional[int] = None
    project_type_id: Optional[int] = None
    difficulty_level_id: Optional[int] = None
    image_url: Optional[str] = None
    git_url: Optional[str] = None
    deploy_url: Optional[str] = None


class Project(ProjectBase, table=True):
    __tablename__: str = "portfolio_project"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_type: ProjectType = Relationship(back_populates="projects")
    difficulty_level: DifficultyLevel = Relationship(back_populates="projects")
    translations: List[ProjectTranslation] = Relationship(back_populates="project", cascade_delete=True)
    tags: List[Tag] = Relationship(back_populates="projects", link_model=ProjectTag)


class ProjectReadComplete(ProjectBaseFlat):
    id: int
    project_type: Optional[ProjectTypeRead] = None
    difficulty_level: Optional[DifficultyLevelRead] = None
    tags: List[TagRead] = []
    translations: List[ProjectTranslationRead] = []


# --- Core Entity: Course ---
class CourseBaseFlat(SQLModel):
    year: int
    url: Optional[str] = None


class CourseBase(CourseBaseFlat):
    academy_id: int = Field(foreign_key="portfolio_academy.id")
    category_id: int = Field(foreign_key="portfolio_category.id")


class CourseCreate(CourseBase):
    pass


class CourseUpdate(SQLModel):
    year: Optional[int] = None
    url: Optional[str] = None
    academy_id: Optional[int] = None
    category_id: Optional[int] = None


class CourseRead(CourseBase):
    id: int


class Course(CourseBase, table=True):
    __tablename__: str = "portfolio_course"
    id: Optional[int] = Field(default=None, primary_key=True)
    academy: Academy = Relationship(back_populates="courses")
    tags: List[Tag] = Relationship(back_populates="courses", link_model=CourseTag)
    category: Category = Relationship(back_populates="courses")
    translations: List[CourseTranslation] = Relationship(back_populates="course", cascade_delete=True)


class CourseReadComplete(CourseBaseFlat):
    id: int
    academy: Optional[AcademyRead] = None
    category: Optional[CategoryRead] = None
    tags: List[TagRead] = []
    translations: List[CourseTranslationRead] = []


# --- Core Entity: Certification ---
class CertificationBaseFlat(SQLModel):
    year: int
    validation_serial: Optional[str] = None
    url: Optional[str] = None


class CertificationBase(CertificationBaseFlat):
    academy_id: int = Field(foreign_key="portfolio_academy.id")
    category_id: int = Field(foreign_key="portfolio_category.id")


class CertificationCreate(CertificationBase):
    pass


class CertificationUpdate(SQLModel):
    year: Optional[int] = None
    validation_serial: Optional[str] = None
    url: Optional[str] = None
    academy_id: Optional[int] = None
    category_id: Optional[int] = None


class CertificationRead(CertificationBase):
    id: int


class Certification(CertificationBase, table=True):
    __tablename__: str = "portfolio_certification"
    id: Optional[int] = Field(default=None, primary_key=True)
    academy: Academy = Relationship(back_populates="certifications")
    category: Category = Relationship(back_populates="certifications")
    translations: List[CertificationTranslation] = Relationship(back_populates="certification", cascade_delete=True)
    tags: List[Tag] = Relationship(back_populates="certifications", link_model=CertificationTag)


class CertificationReadComplete(CertificationBaseFlat):
    id: int
    academy: Optional[AcademyRead] = None
    category: Optional[CategoryRead] = None
    tags: List[TagRead] = []
    translations: List[CertificationTranslationRead] = []
