from enum import Enum
from typing import Annotated, Optional, List
from pydantic import StringConstraints
from sqlmodel import SQLModel, Field, Relationship


# ==========================================
# 1. AUXILIARY MODELS & CONFIG
# ==========================================

class ProjectTypeBase(SQLModel):
    name: str = Field(unique=True, index=True, min_length=1)


class ProjectType(ProjectTypeBase, table=True):
    __tablename__: str = "portfolio_project_type"
    id: Optional[int] = Field(default=None, primary_key=True)
    projects: List["Project"] = Relationship(back_populates="project_type")


class DifficultyLevelBase(SQLModel):
    name: str = Field(unique=True, index=True, min_length=1)


class DifficultyLevel(DifficultyLevelBase, table=True):
    __tablename__: str = "portfolio_difficulty_level"
    id: Optional[int] = Field(default=None, primary_key=True)
    projects: List["Project"] = Relationship(back_populates="difficulty_level")


class CategoryBase(SQLModel):
    name: str = Field(unique=True, index=True, min_length=1)


class Category(CategoryBase, table=True):
    __tablename__: str = "portfolio_category"
    id: Optional[int] = Field(default=None, primary_key=True)
    courses: List["Course"] = Relationship(back_populates="category")
    certifications: List["Certification"] = Relationship(back_populates="category")


class LanguageBase(SQLModel):
    code: str = Field(primary_key=True, min_length=2, max_length=2)
    name: str = Field(unique=True, min_length=2)


class Language(LanguageBase, table=True):
    __tablename__: str = "portfolio_language"
    project_translations: List["ProjectTranslation"] = Relationship(back_populates="language")
    course_translations: List["CourseTranslation"] = Relationship(back_populates="language")
    certification_translations: List["CertificationTranslation"] = Relationship(back_populates="language")


# ==========================================
# 2. MANY-TO-MANY LINK MODELS
# ==========================================

class ProjectTag(SQLModel, table=True):
    __tablename__: str = "portfolio_project_tag"
    project_id: int = Field(foreign_key="portfolio_project.id", ondelete="CASCADE", primary_key=True)
    tag_id: int = Field(foreign_key="portfolio_tag.id", ondelete="CASCADE", primary_key=True)


class CourseTag(SQLModel, table=True):
    __tablename__: str = "portfolio_course_tag"
    course_id: int = Field(foreign_key="portfolio_course.id", ondelete="CASCADE", primary_key=True)
    tag_id: int = Field(foreign_key="portfolio_tag.id", ondelete="CASCADE", primary_key=True)


# ==========================================
# 3. TAG MODELS
# ==========================================

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
    courses: List["Course"] = Relationship(back_populates="tags", link_model=CourseTag)


# ==========================================
# 4. PROJECT & TRANSLATION MODELS
# ==========================================

class ProjectBase(SQLModel):
    year: int = Field(index=True)
    project_type_id: int = Field(foreign_key="portfolio_project_type.id")
    difficulty_id: int = Field(foreign_key="portfolio_difficulty_level.id")
    image_url: Optional[str] = None
    git_url: Optional[str] = None
    deploy_url: Optional[str] = None


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(SQLModel):
    year: Optional[int] = None
    project_type_id: Optional[int] = None
    difficulty_id: Optional[int] = None
    image_url: Optional[str] = None
    git_url: Optional[str] = None
    deploy_url: Optional[str] = None


class Project(ProjectBase, table=True):
    __tablename__: str = "portfolio_project"
    id: Optional[int] = Field(default=None, primary_key=True)
    project_type: ProjectType = Relationship(back_populates="projects")
    difficulty_level: DifficultyLevel = Relationship(back_populates="projects")
    translations: List["ProjectTranslation"] = Relationship(back_populates="project", cascade_delete=True)
    tags: List[Tag] = Relationship(back_populates="projects", link_model=ProjectTag)


class ProjectTranslationBase(SQLModel):
    title: str = Field(min_length=2)
    description: str = Field(min_length=10)


class ProjectTranslationCreate(ProjectTranslationBase):
    language_code: str
    project_id: int


class ProjectTranslationUpdate(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None


class ProjectTranslation(ProjectTranslationBase, table=True):
    __tablename__: str = "portfolio_project_translation"
    language_code: str = Field(foreign_key="portfolio_language.code", ondelete="RESTRICT", primary_key=True)
    project_id: int = Field(foreign_key="portfolio_project.id", ondelete="CASCADE", primary_key=True)
    project: Project = Relationship(back_populates="translations")
    language: Language = Relationship(back_populates="project_translations")


# ==========================================
# 5. ACADEMY & COURSE MODELS
# ==========================================

class AcademyBase(SQLModel):
    name: str = Field(min_length=2, index=True)


class AcademyCreate(AcademyBase):
    pass


class AcademyUpdate(SQLModel):
    name: Optional[str] = None


class Academy(AcademyBase, table=True):
    __tablename__: str = "portfolio_academy"
    id: Optional[int] = Field(default=None, primary_key=True)
    courses: List["Course"] = Relationship(back_populates="academy")
    certifications: List["Certification"] = Relationship(back_populates="academy")


class CourseBase(SQLModel):
    title: str = Field(min_length=3)
    year: int = Field(index=True)
    url: Optional[str] = None
    academy_id: int = Field(foreign_key="portfolio_academy.id")
    category_id: int = Field(foreign_key="portfolio_category.id")


class CourseCreate(CourseBase):
    pass


class CourseUpdate(SQLModel):
    title: Optional[str] = None
    year: Optional[int] = None
    url: Optional[str] = None
    academy_id: Optional[int] = None
    category_id: Optional[int] = None


class Course(CourseBase, table=True):
    __tablename__: str = "portfolio_course"
    id: Optional[int] = Field(default=None, primary_key=True)
    academy: Academy = Relationship(back_populates="courses")
    tags: List[Tag] = Relationship(back_populates="courses", link_model=CourseTag)
    category: Category = Relationship(back_populates="courses")
    translations: List["CourseTranslation"] = Relationship(back_populates="course", cascade_delete=True)


class CourseTranslationBase(SQLModel):
    title: str = Field(min_length=3)


class CourseTranslationCreate(CourseTranslationBase):
    language_code: str
    course_id: int


class CourseTranslationUpdate(SQLModel):
    title: Optional[str] = None


class CourseTranslation(CourseTranslationBase, table=True):
    __tablename__: str = "portfolio_course_translation"
    language_code: str = Field(foreign_key="portfolio_language.code", ondelete="RESTRICT", primary_key=True)
    course_id: int = Field(foreign_key="portfolio_course.id", ondelete="CASCADE", primary_key=True)
    course: Course = Relationship(back_populates="translations")
    language: Language = Relationship(back_populates="course_translations")


# ==========================================
# 6. CERTIFICATION MODELS
# ==========================================

class CertificationBase(SQLModel):
    year: int = Field(index=True)
    validation_serial: Optional[str] = Field(default=None, unique=True)
    url: Optional[str] = None
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


class Certification(CertificationBase, table=True):
    __tablename__: str = "portfolio_certification"
    id: Optional[int] = Field(default=None, primary_key=True)
    academy: Academy = Relationship(back_populates="certifications")
    category: Category = Relationship(back_populates="certifications")
    translations: List["CertificationTranslation"] = Relationship(back_populates="certification", cascade_delete=True)


class CertificationTranslationBase(SQLModel):
    title: str = Field(min_length=3)


class CertificationTranslationCreate(CertificationTranslationBase):
    language_code: str
    certification_id: int


class CertificationTranslationUpdate(SQLModel):
    title: Optional[str] = None


class CertificationTranslation(CertificationTranslationBase, table=True):
    __tablename__: str = "portfolio_certification_translation"
    language_code: str = Field(foreign_key="portfolio_language.code", ondelete="RESTRICT", primary_key=True)
    certification_id: int = Field(foreign_key="portfolio_certification.id", ondelete="CASCADE", primary_key=True)
    certification: Certification = Relationship(back_populates="translations")
    language: Language = Relationship(back_populates="certification_translations")
