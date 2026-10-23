IF OBJECT_ID(N'dbo.BusinessContextResource', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.BusinessContextResource (
        ResourceKey nvarchar(128) NOT NULL PRIMARY KEY,
        TenantId uniqueidentifier NOT NULL,
        ResourceId nvarchar(2048) NOT NULL,
        DisplayName nvarchar(256) NOT NULL,
        IsAssignable bit NOT NULL,
        HasCompleteSourceTags bit NOT NULL,
        Version int NOT NULL CONSTRAINT DF_BusinessContextResource_Version DEFAULT (1),
        LastSynchronizedAt datetime2(3) NOT NULL,
        CONSTRAINT UQ_BusinessContextResource_Tenant_Resource UNIQUE (TenantId, ResourceId)
    );
END;
GO

IF OBJECT_ID(N'dbo.BusinessContextOption', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.BusinessContextOption (
        ContextOptionId uniqueidentifier NOT NULL PRIMARY KEY,
        TenantId uniqueidentifier NOT NULL,
        BusinessUnit nvarchar(200) NOT NULL,
        Initiative nvarchar(300) NOT NULL,
        Project nvarchar(300) NOT NULL,
        Tool nvarchar(200) NOT NULL,
        IsActive bit NOT NULL CONSTRAINT DF_BusinessContextOption_IsActive DEFAULT (1),
        IsDemoFixture bit NOT NULL CONSTRAINT DF_BusinessContextOption_IsDemoFixture DEFAULT (0),
        CONSTRAINT UQ_BusinessContextOption_Tenant_Values UNIQUE (TenantId, BusinessUnit, Initiative, Project, Tool)
    );
END;
GO

IF OBJECT_ID(N'dbo.BusinessContextGrant', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.BusinessContextGrant (
        TenantId uniqueidentifier NOT NULL,
        ActorObjectId uniqueidentifier NOT NULL,
        ResourceKey nvarchar(128) NOT NULL,
        ContextOptionId uniqueidentifier NOT NULL,
        ValidFrom date NOT NULL,
        ValidToExclusive date NULL,
        IsActive bit NOT NULL CONSTRAINT DF_BusinessContextGrant_IsActive DEFAULT (1),
        CONSTRAINT PK_BusinessContextGrant PRIMARY KEY (TenantId, ActorObjectId, ResourceKey, ContextOptionId, ValidFrom),
        CONSTRAINT FK_BusinessContextGrant_Resource FOREIGN KEY (ResourceKey) REFERENCES dbo.BusinessContextResource(ResourceKey),
        CONSTRAINT FK_BusinessContextGrant_Option FOREIGN KEY (ContextOptionId) REFERENCES dbo.BusinessContextOption(ContextOptionId),
        CONSTRAINT CK_BusinessContextGrant_Validity CHECK (ValidToExclusive IS NULL OR ValidToExclusive > ValidFrom)
    );
END;
GO

IF OBJECT_ID(N'dbo.BusinessContextAssignment', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.BusinessContextAssignment (
        AssignmentId bigint IDENTITY(1,1) NOT NULL PRIMARY KEY,
        TenantId uniqueidentifier NOT NULL,
        ResourceKey nvarchar(128) NOT NULL,
        ContextOptionId uniqueidentifier NOT NULL,
        EffectiveFrom date NOT NULL,
        Reason nvarchar(1000) NOT NULL,
        ActorObjectId uniqueidentifier NOT NULL,
        InvocationId uniqueidentifier NOT NULL,
        RequestHash char(64) NOT NULL,
        ResourceVersion int NOT NULL,
        IsDemoFixture bit NOT NULL CONSTRAINT DF_BusinessContextAssignment_IsDemoFixture DEFAULT (0),
        CreatedAtUtc datetime2(3) NOT NULL CONSTRAINT DF_BusinessContextAssignment_CreatedAt DEFAULT (SYSUTCDATETIME()),
        PreviousContextOptionId uniqueidentifier NULL,
        CONSTRAINT UQ_BusinessContextAssignment_Effective UNIQUE (TenantId, ResourceKey, EffectiveFrom),
        CONSTRAINT UQ_BusinessContextAssignment_Invocation UNIQUE (TenantId, InvocationId),
        CONSTRAINT FK_BusinessContextAssignment_Resource FOREIGN KEY (ResourceKey) REFERENCES dbo.BusinessContextResource(ResourceKey),
        CONSTRAINT FK_BusinessContextAssignment_Option FOREIGN KEY (ContextOptionId) REFERENCES dbo.BusinessContextOption(ContextOptionId)
    );
END;
GO

IF OBJECT_ID(N'dbo.BusinessContextConfiguration', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.BusinessContextConfiguration (
        TenantId uniqueidentifier NOT NULL PRIMARY KEY,
        DemoFixturesEnabled bit NOT NULL CONSTRAINT DF_BusinessContextConfiguration_DemoFixtures DEFAULT (0),
        UpdatedAtUtc datetime2(3) NOT NULL CONSTRAINT DF_BusinessContextConfiguration_UpdatedAt DEFAULT (SYSUTCDATETIME())
    );
END;
GO

CREATE OR ALTER PROCEDURE dbo.AssignBusinessContext
    @TenantId uniqueidentifier,
    @ActorObjectId uniqueidentifier,
    @InvocationId uniqueidentifier,
    @RequestHash char(64),
    @ResourceKey nvarchar(128),
    @ContextOptionId uniqueidentifier,
    @EffectiveFrom date,
    @Reason nvarchar(1000),
    @ExpectedVersion int
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @CurrentVersion int;
    DECLARE @PreviousContextOptionId uniqueidentifier;
    DECLARE @ExistingHash char(64);
    DECLARE @IsDemoFixture bit;
    DECLARE @Status nvarchar(40);

    BEGIN TRY
        BEGIN TRANSACTION;

        SELECT @CurrentVersion = Version
        FROM dbo.BusinessContextResource WITH (UPDLOCK, HOLDLOCK)
        WHERE TenantId = @TenantId AND ResourceKey = @ResourceKey;

        IF @CurrentVersion IS NULL
        BEGIN
            SET @Status = N'resource_not_assignable';
        END
        ELSE
        BEGIN
            SELECT @ExistingHash = RequestHash
            FROM dbo.BusinessContextAssignment
            WHERE TenantId = @TenantId AND InvocationId = @InvocationId;

            IF @ExistingHash IS NOT NULL
                SET @Status = CASE WHEN @ExistingHash = @RequestHash THEN N'already_saved' ELSE N'invocation_conflict' END;
            ELSE IF EXISTS (
                SELECT 1 FROM dbo.BusinessContextAssignment
                WHERE TenantId = @TenantId AND ActorObjectId = @ActorObjectId AND RequestHash = @RequestHash
            )
                SET @Status = N'already_saved';
            ELSE IF EXISTS (
                SELECT 1 FROM dbo.BusinessContextResource
                WHERE TenantId = @TenantId AND ResourceKey = @ResourceKey
                  AND (IsAssignable = 0 OR HasCompleteSourceTags = 1)
            )
                SET @Status = N'resource_not_assignable';
            ELSE IF @CurrentVersion <> @ExpectedVersion
                SET @Status = N'stale_version';
            ELSE IF EXISTS (
                SELECT 1 FROM dbo.BusinessContextAssignment
                WHERE TenantId = @TenantId AND ResourceKey = @ResourceKey AND EffectiveFrom = @EffectiveFrom
            )
                SET @Status = N'effective_date_conflict';
            ELSE IF NOT EXISTS (
                SELECT 1
                FROM dbo.BusinessContextGrant AS g
                INNER JOIN dbo.BusinessContextOption AS o
                    ON o.TenantId = g.TenantId AND o.ContextOptionId = g.ContextOptionId AND o.IsActive = 1
                WHERE g.TenantId = @TenantId AND g.ActorObjectId = @ActorObjectId
                  AND g.ResourceKey = @ResourceKey AND g.ContextOptionId = @ContextOptionId
                  AND g.IsActive = 1 AND g.ValidFrom <= @EffectiveFrom
                  AND (g.ValidToExclusive IS NULL OR @EffectiveFrom < g.ValidToExclusive)
            )
                SET @Status = N'not_authorized';
            ELSE
            BEGIN
                SELECT @IsDemoFixture = IsDemoFixture
                FROM dbo.BusinessContextOption
                WHERE TenantId = @TenantId AND ContextOptionId = @ContextOptionId;

                SELECT TOP (1) @PreviousContextOptionId = ContextOptionId
                FROM dbo.BusinessContextAssignment
                WHERE TenantId = @TenantId AND ResourceKey = @ResourceKey AND EffectiveFrom < @EffectiveFrom
                ORDER BY EffectiveFrom DESC;

                UPDATE dbo.BusinessContextResource
                SET Version = Version + 1
                WHERE TenantId = @TenantId AND ResourceKey = @ResourceKey AND Version = @ExpectedVersion;

                IF @@ROWCOUNT <> 1
                    SET @Status = N'stale_version';
                ELSE
                BEGIN
                    INSERT dbo.BusinessContextAssignment (
                        TenantId, ResourceKey, ContextOptionId, EffectiveFrom, Reason,
                        ActorObjectId, InvocationId, RequestHash, ResourceVersion, IsDemoFixture, PreviousContextOptionId
                    ) VALUES (
                        @TenantId, @ResourceKey, @ContextOptionId, @EffectiveFrom, @Reason,
                        @ActorObjectId, @InvocationId, @RequestHash, @ExpectedVersion + 1, @IsDemoFixture, @PreviousContextOptionId
                    );
                    SET @Status = N'saved';
                END;
            END;
        END;

        IF @Status = N'saved'
            COMMIT TRANSACTION;
        ELSE
            ROLLBACK TRANSACTION;

        SELECT @Status AS Status,
               CASE WHEN @Status = N'saved' THEN @ExpectedVersion + 1 ELSE @CurrentVersion END AS ResourceVersion;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER VIEW dbo.BusinessContextCurrent
AS
    SELECT r.TenantId, r.ResourceKey, r.ResourceId, r.DisplayName, r.Version AS ResourceVersion,
           a.ContextOptionId, o.BusinessUnit, o.Initiative, o.Project, o.Tool, a.EffectiveFrom
    FROM dbo.BusinessContextResource AS r
    OUTER APPLY (
        SELECT TOP (1) assignment.ContextOptionId, assignment.EffectiveFrom
        FROM dbo.BusinessContextAssignment AS assignment
        WHERE assignment.TenantId = r.TenantId AND assignment.ResourceKey = r.ResourceKey
          AND assignment.EffectiveFrom <= CONVERT(date, SYSUTCDATETIME())
        ORDER BY assignment.EffectiveFrom DESC
    ) AS a
    LEFT JOIN dbo.BusinessContextOption AS o
        ON o.TenantId = r.TenantId AND o.ContextOptionId = a.ContextOptionId;
GO