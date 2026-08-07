from sqlmodel import Field, Relationship, SQLModel

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
    project_id: int = Field(
        foreign_key="portfolio_project.id", ondelete="CASCADE", primary_key=True
    )
    tag_id: int = Field(foreign_key="portfolio_tag.id", ondelete="CASCADE", primary_key=True)


class CourseTag(SQLModel, table=True):
    __tablename__: str = "portfolio_course_tag"
    course_id: int = Field(foreign_key="portfolio_course.id", ondelete="CASCADE", primary_key=True)
    tag_id: int = Field(foreign_key="portfolio_tag.id", ondelete="CASCADE", primary_key=True)


class CertificationTag(SQLModel, table=True):
    __tablename__: str = "portfolio_certification_tag"
    certification_id: int = Field(
        foreign_key="portfolio_certification.id", ondelete="CASCADE", primary_key=True
    )
    tag_id: int = Field(foreign_key="portfolio_tag.id", ondelete="CASCADE", primary_key=True)


# ==============================================================================
# LAYER 2: INDEPENDENT AUXILIARY MODELS
# ==============================================================================

# ProjectType, DifficultyLevel, Category are seeded values —


# --- Project Type ---
class ProjectTypeBase(SQLModel):
    name: str = Field(unique=True, index=True, min_length=1)


class ProjectTypeCreate(ProjectTypeBase):
    pass


class ProjectTypeUpdate(SQLModel):
    name: str | None = None


class ProjectTypeRead(ProjectTypeBase):
    id: int


class ProjectType(ProjectTypeBase, table=True):
    __tablename__: str = "portfolio_project_type"
    id: int | None = Field(default=None, primary_key=True)
    projects: list["Project"] = Relationship(back_populates="project_type")


# --- Difficulty Level ---
class DifficultyLevelBase(SQLModel):
    name: str = Field(unique=True, index=True, min_length=1)


class DifficultyLevelCreate(DifficultyLevelBase):
    pass


class DifficultyLevelUpdate(SQLModel):
    name: str | None = None


class DifficultyLevelRead(DifficultyLevelBase):
    id: int


class DifficultyLevel(DifficultyLevelBase, table=True):
    __tablename__: str = "portfolio_difficulty_level"
    id: int | None = Field(default=None, primary_key=True)
    projects: list["Project"] = Relationship(back_populates="difficulty_level")


# --- Category ---
class CategoryBase(SQLModel):
    name: str = Field(unique=True, index=True, min_length=1)


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(SQLModel):
    name: str | None = None


class CategoryRead(CategoryBase):
    id: int


class Category(CategoryBase, table=True):
    __tablename__: str = "portfolio_category"
    id: int | None = Field(default=None, primary_key=True)
    courses: list["Course"] = Relationship(back_populates="category")
    certifications: list["Certification"] = Relationship(back_populates="category")


# --- Language ---
class LanguageBase(SQLModel):
    code: str = Field(primary_key=True, min_length=2, max_length=2)
    name: str = Field(unique=True, min_length=2)


class LanguageCreate(LanguageBase):
    pass


class LanguageUpdate(SQLModel):
    # `code` is the primary key and stays immutable.
    name: str | None = None


class LanguageRead(LanguageBase):
    pass


# `language_code` is part of each translation's composite primary key, so the
# ORM's default "null out the child FK on delete" is impossible and raises an
# AssertionError before the database's RESTRICT is ever reached. Handing the
# decision to the database lets it reject the delete properly, as a 409.
_DEFER_TO_DB = {"passive_deletes": "all"}


class Language(LanguageBase, table=True):
    __tablename__: str = "portfolio_language"

    project_translations: list["ProjectTranslation"] = Relationship(
        back_populates="language", sa_relationship_kwargs=_DEFER_TO_DB
    )
    course_translations: list["CourseTranslation"] = Relationship(
        back_populates="language", sa_relationship_kwargs=_DEFER_TO_DB
    )
    certification_translations: list["CertificationTranslation"] = Relationship(
        back_populates="language", sa_relationship_kwargs=_DEFER_TO_DB
    )


# --- Academy ---
class AcademyBase(SQLModel):
    name: str = Field(min_length=2, index=True)


class AcademyCreate(AcademyBase):
    pass


class AcademyUpdate(SQLModel):
    name: str | None = None


class AcademyRead(AcademyBase):
    id: int


class Academy(AcademyBase, table=True):
    __tablename__: str = "portfolio_academy"
    id: int | None = Field(default=None, primary_key=True)
    courses: list["Course"] = Relationship(back_populates="academy")
    certifications: list["Certification"] = Relationship(back_populates="academy")


# --- Tag ---
class TagBase(SQLModel):
    name: str = Field(unique=True, index=True, min_length=1)


class TagCreate(TagBase):
    pass


class TagUpdate(SQLModel):
    name: str | None = None


class TagRead(TagBase):
    id: int


class Tag(TagBase, table=True):
    __tablename__: str = "portfolio_tag"
    id: int | None = Field(default=None, primary_key=True)
    projects: list["Project"] = Relationship(back_populates="tags", link_model=ProjectTag)
    courses: list["Course"] = Relationship(back_populates="tags", link_model=CourseTag)
    certifications: list["Certification"] = Relationship(
        back_populates="tags", link_model=CertificationTag
    )


# ==============================================================================
# LAYER 3: TRANSLATION MODELS
# ==============================================================================


# --- Project Translation ---
class ProjectTranslationBase(SQLModel):
    title: str = Field(min_length=2)
    description: str = Field(min_length=10)


class ProjectTranslationCreate(ProjectTranslationBase):
    language_code: str


class ProjectTranslationUpdate(SQLModel):
    title: str | None = None
    description: str | None = None


class ProjectTranslationRead(ProjectTranslationBase):
    language_code: str


class ProjectTranslation(ProjectTranslationBase, table=True):
    __tablename__: str = "portfolio_project_translation"
    language_code: str = Field(
        foreign_key="portfolio_language.code", ondelete="RESTRICT", primary_key=True
    )
    project_id: int = Field(
        foreign_key="portfolio_project.id", ondelete="CASCADE", primary_key=True
    )
    project: "Project" = Relationship(back_populates="translations")
    language: Language = Relationship(back_populates="project_translations")


# --- Course Translation ---
class CourseTranslationBase(SQLModel):
    title: str = Field(min_length=3)


class CourseTranslationCreate(CourseTranslationBase):
    language_code: str


class CourseTranslationUpdate(SQLModel):
    title: str | None = None


class CourseTranslationRead(CourseTranslationBase):
    language_code: str


class CourseTranslation(CourseTranslationBase, table=True):
    __tablename__: str = "portfolio_course_translation"
    language_code: str = Field(
        foreign_key="portfolio_language.code", ondelete="RESTRICT", primary_key=True
    )
    course_id: int = Field(foreign_key="portfolio_course.id", ondelete="CASCADE", primary_key=True)
    course: "Course" = Relationship(back_populates="translations")
    language: Language = Relationship(back_populates="course_translations")


# --- Certification Translation ---
class CertificationTranslationBase(SQLModel):
    title: str = Field(min_length=3)


class CertificationTranslationCreate(CertificationTranslationBase):
    language_code: str


class CertificationTranslationUpdate(SQLModel):
    title: str | None = None


class CertificationTranslationRead(CertificationTranslationBase):
    language_code: str


class CertificationTranslation(CertificationTranslationBase, table=True):
    __tablename__: str = "portfolio_certification_translation"
    language_code: str = Field(
        foreign_key="portfolio_language.code", ondelete="RESTRICT", primary_key=True
    )
    certification_id: int = Field(
        foreign_key="portfolio_certification.id", ondelete="CASCADE", primary_key=True
    )
    certification: "Certification" = Relationship(back_populates="translations")
    language: Language = Relationship(back_populates="certification_translations")


# ==============================================================================
# LAYER 4: CORE ENTITIES & COMPLETE READ SCHEMAS
# ==============================================================================


# --- Core Entity: Project ---
class ProjectBaseFlat(SQLModel):
    year: int = Field(index=True)
    is_main: bool = Field(default=False, index=True)
    image_url: str | None = None
    git_url: str | None = None
    deploy_url: str | None = None


class ProjectBase(ProjectBaseFlat):
    project_type_id: int = Field(foreign_key="portfolio_project_type.id")
    difficulty_level_id: int = Field(foreign_key="portfolio_difficulty_level.id")


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(SQLModel):
    year: int | None = None
    is_main: bool | None = None
    project_type_id: int | None = None
    difficulty_level_id: int | None = None
    image_url: str | None = None
    git_url: str | None = None
    deploy_url: str | None = None


class Project(ProjectBase, table=True):
    __tablename__: str = "portfolio_project"
    id: int | None = Field(default=None, primary_key=True)
    project_type: ProjectType = Relationship(back_populates="projects")
    difficulty_level: DifficultyLevel = Relationship(back_populates="projects")
    translations: list[ProjectTranslation] = Relationship(
        back_populates="project", cascade_delete=True
    )
    tags: list[Tag] = Relationship(back_populates="projects", link_model=ProjectTag)


class ProjectReadComplete(ProjectBaseFlat):
    id: int
    project_type: ProjectTypeRead | None = None
    difficulty_level: DifficultyLevelRead | None = None
    tags: list[TagRead] = []
    translations: list[ProjectTranslationRead] = []


# --- Core Entity: Course ---
class CourseBaseFlat(SQLModel):
    year: int
    url: str | None = None


class CourseBase(CourseBaseFlat):
    academy_id: int = Field(foreign_key="portfolio_academy.id")
    category_id: int = Field(foreign_key="portfolio_category.id")


class CourseCreate(CourseBase):
    pass


class CourseUpdate(SQLModel):
    year: int | None = None
    url: str | None = None
    academy_id: int | None = None
    category_id: int | None = None


class CourseRead(CourseBase):
    id: int


class Course(CourseBase, table=True):
    __tablename__: str = "portfolio_course"
    id: int | None = Field(default=None, primary_key=True)
    academy: Academy = Relationship(back_populates="courses")
    tags: list[Tag] = Relationship(back_populates="courses", link_model=CourseTag)
    category: Category = Relationship(back_populates="courses")
    translations: list[CourseTranslation] = Relationship(
        back_populates="course", cascade_delete=True
    )


class CourseReadComplete(CourseBaseFlat):
    id: int
    academy: AcademyRead | None = None
    category: CategoryRead | None = None
    tags: list[TagRead] = []
    translations: list[CourseTranslationRead] = []


# --- Core Entity: Certification ---
class CertificationBaseFlat(SQLModel):
    year: int
    validation_serial: str | None = None
    url: str | None = None


class CertificationBase(CertificationBaseFlat):
    academy_id: int = Field(foreign_key="portfolio_academy.id")
    category_id: int = Field(foreign_key="portfolio_category.id")


class CertificationCreate(CertificationBase):
    pass


class CertificationUpdate(SQLModel):
    year: int | None = None
    validation_serial: str | None = None
    url: str | None = None
    academy_id: int | None = None
    category_id: int | None = None


class CertificationRead(CertificationBase):
    id: int


class Certification(CertificationBase, table=True):
    __tablename__: str = "portfolio_certification"
    id: int | None = Field(default=None, primary_key=True)
    academy: Academy = Relationship(back_populates="certifications")
    category: Category = Relationship(back_populates="certifications")
    translations: list[CertificationTranslation] = Relationship(
        back_populates="certification", cascade_delete=True
    )
    tags: list[Tag] = Relationship(back_populates="certifications", link_model=CertificationTag)


class CertificationReadComplete(CertificationBaseFlat):
    id: int
    academy: AcademyRead | None = None
    category: CategoryRead | None = None
    tags: list[TagRead] = []
    translations: list[CertificationTranslationRead] = []
